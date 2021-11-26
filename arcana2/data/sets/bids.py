import attr
import re
import json
import os.path
import tempfile
import docker
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from pydra import Workflow, mark
from pydra.engine.task import (
    FunctionTask, DockerTask, SingularityTask, ShellCommandTask)
from pydra.engine.specs import (
    BaseSpec, SpecInfo, DockerSpec, SingularitySpec, ShellOutSpec)
from arcana2.core.data.set import Dataset
from arcana2.data.types.general import directory
from arcana2.data.spaces.clinical import Clinical
from ..repositories import FileSystem
from arcana2.exceptions import ArcanaUsageError, ArcanaEmptyDatasetError


@dataclass
class SourceMetadata():

    name: str
    version: str
    description: str
    url: str
    container: str

    def to_bids(self):
        return {
            'Name': self.name,
            'Version': self.version,
            'Description': self.description,
            'CodeURL': self.url,
            'Container': self.container}

    @classmethod
    def from_bids(cls, dct):
        return SourceMetadata(
            name=dct['Name'],
            version=dct['Version'],
            description=dct['Description'],
            url=dct['CodeURL'],
            container=dct['Container'])


@attr.s
class BidsDataset(Dataset):
    """ A representation of a "dataset" in Brain Imaging Data Structure (BIDS)
    format
    """
    name: str = attr.ib(default='Autogenerated-dataset')
    participants: dict[str, dict[str, str]] = attr.ib(factory=dict, repr=False)
    acknowledgements: str = attr.ib(default="Generic BIDS dataset", repr=False)
    authors: list[str] = attr.ib(default=[], repr=False)
    bids_version: str = attr.ib(default='1.0.1', repr=False)
    doi: str = attr.ib(default=None, repr=False)
    funding: list[str] = attr.ib(factory=list, repr=False)
    bids_type: str = attr.ib(default='derivative', repr=False)
    license: str = attr.ib(default='CC0', repr=False)
    references: list[str] = attr.ib(factory=list)
    how_to_acknowledge: str = attr.ib(default="see licence")
    ethics_approvals: list[str] = attr.ib(factory=list)
    generated_by: list = attr.ib(factory=list)
    sources: list[SourceMetadata] = attr.ib(factory=list)

    @classmethod
    def load(cls, path):
        if list(Path(path).glob('**/sub-*/ses-*')):
            hierarchy = [Clinical.subject, Clinical.session]
        else:
            hierarchy = [Clinical.session]    
        dataset = BidsDataset(path, repository=BidsFormat(),
                              hierarchy=hierarchy)
        dataset.load_metadata()
        return dataset

    @classmethod
    def create(cls, path, name, subject_ids, session_ids=None, **kwargs):
        path = Path(path)
        path.mkdir()
        if session_ids is not None:
            hierarchy = [Clinical.subject, Clinical.timepoint]
        else:
            hierarchy = [Clinical.session]
        dataset = BidsDataset(
            path, repository=BidsFormat(), hierarchy=hierarchy,
            name=name, **kwargs)
        # Create nodes
        for subject_id in subject_ids:
            if not subject_id.startswith('sub-'):
                subject_id = f'sub-{subject_id}'
            dataset.participants[subject_id] = {}
            if session_ids:
                for session_id in session_ids:
                    if not session_id.startswith('sub-'):
                        session_id = f'ses-{session_id}'
                    node = dataset.add_leaf_node([subject_id, session_id])
                    BidsFormat.absolute_node_path(node).mkdir(parents=True)
            else:
                node = dataset.add_leaf_node([subject_id])
                BidsFormat.absolute_node_path(node).mkdir(parents=True)
        dataset.save_metadata()
        return dataset

    def is_multi_session(self):
        return len(self.hierarchy) > 1

    def save_metadata(self):
        if not self.participants:
            raise ArcanaEmptyDatasetError(
                "Dataset needs at least one participant before the metadata "
                "can be saved")
        dct = {
            'Name': self.name,
            'BIDSVersion': self.bids_version,
            'DatasetType': self.bids_type,
            'Licence': self.license,
            'Authors': self.authors,
            'Acknowledgements': self.acknowledgements,
            'HowToAcknowledge': self.how_to_acknowledge,
            'Funding': self.funding,
            'EthicsApprovals': self.ethics_approvals,
            'ReferencesAndLinks': self.references,
            'DatasetDOI': self.doi}
        if self.bids_type == 'derivative':
            dct['GeneratedBy'] = self.generated_by
            dct['sourceDatasets'] = [d.bids_dict() for d in self.sources]
        with open(self.root_dir / 'dataset_description.json', 'w') as f:
            json.dump(dct, f)

        with open(self.root_dir / 'participants.tsv', 'w') as f:
            col_names = next(iter(self.participants.values())).keys()
            f.write('participant_id\t' + '\t'.join(col_names) + '\n')
            for pcpt_id, pcpt_attrs in self.participants.items():
                f.write(f'{pcpt_id}\t'
                        + '\t'.join(pcpt_attrs[c] for c in col_names) + '\n')

    def load_metadata(self):
        description_json_path = (self.root_dir / 'dataset_description.json')
        if not description_json_path.exists():
            raise ArcanaEmptyDatasetError(
                f"Could not find a directory at '{self.id}' containing a "
                "'dataset_description.json' file")
        with open(description_json_path, 'w') as f:
            dct = json.load(f)               
        self.bids_name = dct['Name']
        self.bids_version = dct['BIDSVersion']
        self.bids_type = dct['DatasetType']
        self.license = dct['Licence']
        self.authors = dct['Authors']
        self.acknowledgements = dct['Acknowledgements']
        self.how_to_acknowledge = dct['HowToAcknowledge']
        self.funding = dct['Funding']
        self.ethics_approvals = dct['EthicsApprovals']
        self.references = dct['ReferencesAndLinks']
        self.doi = dct['DatasetDOI']
        if self.bids_type == 'derivative':
            self.generated_by = dct['GeneratedBy']
            self.sources = [SourceMetadata.from_dict(d)
                            for d in dct['sourceDatasets']]

        self.participants = {}
        with open(self.root_dir / 'participants.tsv') as f:
            cols = f.readline().split('\t')
            while line:= f.readline():
                d = dict(zip(cols, line.split('\t')))
                self.participants[d.pop('participant_id')] = d


class BidsFormat(FileSystem):
    """Repository for working with data stored on the file-system in BIDS format 
    """

    def find_nodes(self, dataset: BidsDataset):
        """
        Find all nodes within the dataset stored in the repository and
        construct the data tree within the dataset

        Parameters
        ----------
        dataset : Dataset
            The dataset to construct the tree dimensions for
        """

        try:
            dataset.load_metadata()
        except ArcanaEmptyDatasetError:
            return

        for subject_id, participant in dataset.participants.items():
            base_ids = {Clinical.group: participant.get('group'),
                        Clinical.subject: subject_id}
            if dataset.is_multi_session():
                for sess_id in (dataset.root_dir / subject_id).iterdir():
                    ids = copy(base_ids)
                    ids[Clinical.timepoint] = sess_id
                    ids[Clinical.session] = subject_id + '_' + sess_id
                    dataset.add_node(ids, Clinical.session)
            else:
                ids = copy(base_ids)
                ids[Clinical.session] = subject_id
                dataset.add_node(ids, Clinical.session)

    def find_items(self, data_node):
        rel_session_path = self.node_path(data_node)
        root_dir = data_node.dataset.root_dir
        session_path = (root_dir / rel_session_path)
        session_path.mkdir(exist_ok=True)
        for modality_dir in session_path.iterdir():
            self.find_items_in_dir(modality_dir, data_node)
        deriv_dir = (root_dir / 'derivatives')
        if deriv_dir.exists():
            for pipeline_dir in deriv_dir.iterdir():
                self.find_items_in_dir(pipeline_dir / rel_session_path,
                                       data_node)        

    def file_group_path(self, file_group):
        fs_path = self.root_dir(file_group.data_node)
        parts = file_group.path.split('/')
        if parts[0] == 'derivatives':
            if len(parts) < 2:
                raise ArcanaUsageError(
                    f"Derivative paths should have at least 3 parts ({file_group.path}")
            elif len(parts) == 2 and file_group.datatype != directory:
                raise ArcanaUsageError(
                    "Derivative paths with 2 parts must be of type directory "
                    f"({file_group.path}")
            fs_path /= parts[0]
            fs_path /= parts[1]
            parts = parts[2:]
        fs_path /= self.node_path(file_group.data_node)
        for part in parts:
            fs_path /= part
        if file_group.datatype.extension:
            fs_path = fs_path.with_suffix(file_group.datatype.extension)
        return fs_path

    def fields_json_path(self, field):
        parts = field.path.split('/')
        if parts[0] != 'derivatives':
            assert False, "Non-derivative fields should be taken from participants.tsv"
        return (field.data_node.dataset.root_dir.joinpath(parts[:2])
                / self.node_path(field.data_node) / self.FIELDS_FNAME)

    def get_field_val(self, field):
        data_node = field.data_node
        dataset = data_node.dataset
        if field.name in dataset.participant_attrs:
            val = dataset.participants[data_node.ids[Clinical.subject]]
        else:
            val = super().get_field_val(field)
        return val

    @classmethod
    def wrap_app(cls,
                 name,
                 image_tag,
                 inputs: dict[str, type],
                 outputs: dict[str, type]=None,
                 frequency: Clinical=Clinical.session,
                 parameters: dict[str, str]=None,
                 container_type: str='docker') -> Workflow:
        """Creates a Pydra workflow which takes inputs and maps them to
        a BIDS dataset, executes a BIDS app and extracts outputs from
        the derivatives stored back in the BIDS dataset

        Parameters
        ----------
        image_tag : str
            Name of the BIDS app image to wrap
        inputs : dict[str, type]
            The inputs to be stored in a BIDS dataset, mapping a sanitized name
            to be added in the workflow input interface and the location within
            the BIDS app to put it
        outputs : dict[str, type]
            The outputs to be extracted from the output directory mounted to the
            BIDS app to be added in the workflow input interface and the location within
            the BIDS app to find it
        parameters : list[tuple[str, dtype]]
            The parameters of the app to be exposed to the interface
        container_type : str
            The container technology to use to run the app (either 'docker' or'singularity')
        Returns
        -------
        pydra.Workflow
            A Pydra workflow 
        """
        if parameters is None:
            parameters = {}
        if outputs is None:
            outputs = {f'derivatives/{name}': directory}
        # Ensure output paths all start with 'derivatives
        input_names = [cls.escape_name(i) for i in inputs]
        output_names = [cls.escape_name(o) for o in outputs]
        workflow = Workflow(
            name=name,
            input_spec=input_names)

        def to_bids(frequency, inputs, app_name, **input_values):
            dataset = BidsDataset.create(
                path=Path(tempfile.mkdtemp()) / 'bids',
                name=app_name + '_dataset',
                subject_ids=[cls.DUMMY_SUBJECT_ID])
            for inpt_path, inpt_type in inputs.items():
                dataset.add_sink(cls.escape_name(inpt_path), inpt_type,
                                 path=inpt_path)
            data_node = dataset.node(frequency, cls.DUMMY_SUBJECT_ID)
            with dataset.repository:
                for inpt_name, inpt_value in input_values.items():
                    node_item = data_node[inpt_name]
                    node_item.put(inpt_value) # Store value/path in repository
            return (dataset, dataset.id)

        # Can't use a decorated function as we need to allow for dynamic
        # arguments
        workflow.add(
            FunctionTask(
                to_bids,
                input_spec=SpecInfo(
                    name='ToBidsInputs', bases=(BaseSpec,), fields=(
                        [('frequency', Clinical),
                        ('inputs', dict[str, type]),
                        ('app_name', str)]
                        + [(i, str) for i in input_names])),
                output_spec=SpecInfo(
                    name='ToBidsOutputs', bases=(BaseSpec,), fields=[
                        ('dataset', BidsDataset),
                        ('dataset_path', Path)]),
                name='to_bids',
                frequency=frequency,
                inputs=inputs,
                app_name=name,
                **{i: getattr(workflow.lzin, i) for i in input_names}))

        app_kwargs = copy(parameters)
        if frequency == Clinical.session:
            app_kwargs['analysis_level'] = 'participant'
            app_kwargs['participant_label'] = cls.DUMMY_SUBJECT_ID
        else:
            app_kwargs['analysis_level'] = 'group'

        @mark.task
        def bindings(dataset_path: Path, app_name: str) -> list[tuple[str, str, str]]:
            deriv_path = dataset_path / 'derivatives' / app_name / cls.DUMMY_SUBJECT_ID
            return [(dataset_path, cls.INTERNAL_DATASET_PATH, 'ro'),
                    (deriv_path, cls.INTERNAL_DERIV_PATH, 'rw')]

        workflow.add(bindings(dataset_path=workflow.to_bids.lzout.dataset_path,
                              app_name=name))
            
        workflow.add(cls.bids_app_task(
            name='bids_app',
            image_tag=image_tag,
            parameters={p: type(p) for p in parameters},
            container_type=container_type,
            bindings=workflow.bindings.lzout.out,
            **app_kwargs))

        @mark.task
        @mark.annotate(
            {'frequency': Clinical,
             'outputs': dict[str, type],
             'return': {o: str for o in output_names}})
        def extract_bids(dataset, frequency, outputs):
            """Selects the items from the dataset corresponding to the input 
            sources and retrieves them from the repository to a cache on 
            the host"""
            output_paths = []
            data_node = dataset.node(frequency, cls.DUMMY_SUBJECT_ID)
            for output_path, output_type in outputs.items():
                dataset.add_sink(cls.escape_name(output_path), output_type,
                                 path='derivatives/' + output_path)
            with dataset.repository:
                for output_name in outputs:
                    item = data_node[cls.escape_name(output_name)]
                    item.get()  # download to host if required
                    output_paths.append(item.value)
            return tuple(output_paths) if len(outputs) > 1 else outputs[0]
        
        workflow.add(extract_bids(
            name='extract_bids',
            dataset=workflow.bids_app.lzout.dataset_path,
            frequency=frequency,
            outputs=outputs))

        for output_name in output_names:
            workflow.set_output(
                (output_name, getattr(workflow.extract_bids.lzout, output_name)))

        return workflow

    @classmethod
    def bids_app_task(cls, name,
                      image_tag: str,
                      bindings: list[tuple[str, str, str]],
                      parameters: dict[str, type]=None,
                      analysis_level: str='participant',
                      container_type: str='docker',
                      **kwargs) -> ShellCommandTask:

        if parameters is None:
            parameters = {}

        dc = docker.from_env()

        dc.images.pull(image_tag)

        image_attrs = dc.api.inspect_image(image_tag)['Config']

        executable = image_attrs['Entrypoint']
        if executable is None:
            executable = image_attrs['Cmd']

        input_fields = [
            ("dataset_path", Path,
                {"help_string": "Path to BIDS dataset",
                 "position": 1,
                 "mandatory": True,
                 "argstr": ""}),
            ("out_dir", Path,
                {"help_string": "Path where outputs will be written",
                  "position": 2,
                  "mandatory": True,
                  "argstr": ""}),
            ("analysis_level", str,
                {"help_string": "The analysis level the app will be run at",
                 "position": 3,
                 "argstr": ""}),
            ("participant_label", list[str],
                {"help_string": "The IDs to include in the analysis",
                 "argstr": "--participant_label ",
                 "position": 4})]

        output_fields = [
            ('dataset_path', Path,
             {'help_string': "Path to BIDS dataset",
              "output_file_template": "{dataset_path}",
              "requires": ['dataset_path']}),
            ('out_dir', Path,
             {'help_string': "Path where outputs were written",
              "output_file_template": "{out_dir}",
              "requires": ['out_dir']})]

        for param, dtype in parameters.items():
            argstr = f'--{param}'
            if dtype is not bool:
                argstr += ' %s'
            input_fields.append((
                param, dtype, {
                    "help_string": f"Optional parameter {param}",
                    "argstr": argstr}))

        if container_type == 'docker':
            task_cls = DockerTask
            base_spec_cls = DockerSpec
        elif container_type == 'singularity':
            task_cls = SingularityTask
            base_spec_cls = SingularitySpec
        else:
            raise ArcanaUsageError(
                f"Unrecognised container type {container_type} "
                "(can be docker or singularity)")

        return task_cls(
            name=name,
            image=image_tag,
            bindings=bindings,
            input_spec=SpecInfo(name="Input", fields=input_fields,
                                bases=(base_spec_cls,)),
            output_spec=SpecInfo(name="Output", fields=output_fields,
                                 bases=(ShellOutSpec,)),
            out_dir=cls.INTERNAL_DERIV_PATH,
            dataset_path=cls.INTERNAL_DATASET_PATH,  # dataset_path,
            analysis_level=analysis_level,
            **kwargs)


    @classmethod
    def escape_name(cls, path):
        """Escape the name of an item by replacing '/' with a valid substring

        Parameters
        ----------
        item : FileGroup | Provenance
            The item to generate a derived name for

        Returns
        -------
        `str`
            The derived name
        """
        return cls.PATH_SEP.join(str(path).split('/'))

    
    @classmethod
    def unescape_name(cls, name):
        return '/'.join(name.split(cls.PATH_SEP))

    PATH_SEP = '__l__'

    # For running 
    INTERNAL_DERIV_PATH = '/outputs'
    INTERNAL_DATASET_PATH = '/bids_dataset'
    DUMMY_SUBJECT_ID = 'sub-01'
