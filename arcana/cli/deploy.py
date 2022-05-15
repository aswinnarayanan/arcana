import logging
from pathlib import Path
import click
import docker.errors
import tempfile
from traceback import format_exc
from arcana.core.cli import cli
from arcana.core.pipeline import Input as PipelineInput, Output as PipelineOutput
from arcana.core.utils import resolve_class, parse_value
from arcana.core.deploy.utils import load_yaml_spec, walk_spec_paths, DOCKER_HUB
from arcana.core.deploy.docs import create_doc
from arcana.core.utils import package_from_module, pydra_asdict
from arcana.deploy.medimage.xnat import build_xnat_cs_image
from arcana.core.data.set import Dataset
from arcana.core.data.store import DataStore
from arcana.exceptions import ArcanaError


logger = logging.getLogger('arcana')


@cli.group()
def deploy():
    pass


@deploy.group()
def xnat():
    pass


@xnat.command(help="""Build a wrapper image specified in a module

SPEC_PATH is the file system path to the specification to build, or directory
containing multiple specifications

DOCKER_ORG is the Docker organisation to build the """)
@click.argument('spec_path', type=click.Path(exists=True, path_type=Path))
@click.argument('docker_org', type=str)
@click.option('--registry', 'docker_registry', default=None,
              help="The Docker registry to deploy the pipeline to")
@click.option('--build_dir', default=None,
              type=click.Path(exists=True, path_type=Path),
              help="Specify the directory to build the Docker image in")
@click.option('--loglevel', default='warning',
              help="The level to display logs at")
@click.option('--use-local-packages/--dont-use-local-packages', type=bool,
              default=False,
              help=("Use locally installed Python packages, instead of pulling "
                    "them down from PyPI"))
@click.option('--install_extras', type=str, default=None,
              help=("Install extras to use when installing Arcana inside the "
                    "container image. Typically only used in tests to provide "
                    "'test' extra"))
@click.option('--raise-errors/--log-errors', type=bool, default=False,
              help=("Raise exceptions instead of logging failures"))
def build(spec_path, docker_org, docker_registry, loglevel, build_dir,
          use_local_packages, install_extras, raise_errors):

    if isinstance(spec_path, bytes):  # FIXME: This shouldn't be necessary
        spec_path = Path(spec_path.decode('utf-8'))  
    if isinstance(build_dir, bytes):  # FIXME: This shouldn't be necessary
        build_dir = Path(build_dir.decode('utf-8'))

    if install_extras:
        install_extras = install_extras.split(',')
    else:
        install_extras = []

    logging.basicConfig(level=getattr(logging, loglevel.upper()))

    for spath in walk_spec_paths(spec_path):
        spec = load_yaml_spec(spath, base_dir=spec_path)

        # Make image tag
        pkg_name = spath.stem.lower()
        tag = '.'.join(spath.relative_to(spec_path).parent.parts + (pkg_name,))
        image_version = str(spec.pop('pkg_version'))
        if 'wrapper_version' in spec:
            image_version += f"-{spec.pop('wrapper_version')}"
        image_tag = f"{docker_org}/{tag}:{image_version}"
        if docker_registry is not None:
            image_tag = docker_registry.lower() + '/' + image_tag
        else:
            docker_registry = DOCKER_HUB

        try:
            build_xnat_cs_image(
                image_tag=image_tag,
                build_dir=build_dir,
                docker_registry=docker_registry,
                use_local_packages=use_local_packages,
                arcana_install_extras=install_extras,
                **{k: v for k, v in spec.items() if not k.startswith('_')})
        except Exception:
            if raise_errors:
                raise
            logger.error("Could not build %s pipeline:\n%s", image_tag, format_exc())
        else:
            click.echo(image_tag)
            logger.info("Successfully built %s pipeline", image_tag)



@deploy.command(name='test', help="""Test container images defined by YAML
specs

Arguments
---------
module_path
    The file system path to the module to build""")
@click.argument('spec_path', type=click.Path(exists=True, path_type=Path))
def test(spec_path):
    # FIXME: Workaround for click 7.x, which improperly handles path_type
    if type(spec_path) is bytes:
        spec_path = Path(spec_path.decode('utf-8'))

    raise NotImplementedError



@deploy.command(name='docs', help="""Build docs for one or more yaml wrappers

SPEC_PATH is the path of a YAML spec file or directory containing one or more such files.

The generated documentation will be saved to OUTPUT.
""")
@click.argument('spec_path', type=click.Path(exists=True, path_type=Path))
@click.argument('output', type=click.Path(path_type=Path))
@click.option('--flatten/--no-flatten', default=False)
@click.option('--loglevel', default='warning',
              help="The level to display logs at")
def build_docs(spec_path, output, flatten, loglevel):
    # FIXME: Workaround for click 7.x, which improperly handles path_type
    if type(spec_path) is bytes:
        spec_path = Path(spec_path.decode('utf-8'))
    if type(output) is bytes:
        output = Path(output.decode('utf-8'))

    logging.basicConfig(level=getattr(logging, loglevel.upper()))

    output.mkdir(parents=True, exist_ok=True)

    for spath in walk_spec_paths(spec_path):
        spec = load_yaml_spec(spath, base_dir=spec_path)
        mod_name = spec['_module_name']
        create_doc(spec, output, mod_name, src_file=spath.relative_to(spec_path), flatten=flatten)
        logging.info("Successfully created docs for %s", mod_name)



@deploy.command(name='required-packages',
                help="""Detect the Python packages required to run the 
specified workflows and return them and their versions""")
@click.argument('workflow_locations', nargs=-1)
def required_packages(workflow_locations):

    required_modules = set()
    for workflow_location in workflow_locations:
        workflow = resolve_class(workflow_location)
        pydra_asdict(workflow, required_modules)

    for pkg in package_from_module(required_modules):
        click.echo(f"{pkg.key}=={pkg.version}")



@deploy.command(name='inspect-docker-exec',
               help="""Extract the executable from a Docker image""")
@click.argument('image_tag', type=str)
def inspect_docker_exec(image_tag):
    """Pulls a given Docker image tag and inspects the image to get its
entrypoint/cmd

IMAGE_TAG is the tag of the Docker image to inspect"""
    dc = docker.from_env()

    dc.images.pull(image_tag)

    image_attrs = dc.api.inspect_image(image_tag)['Config']

    executable = image_attrs['Entrypoint']
    if executable is None:
        executable = image_attrs['Cmd']

    click.echo(executable)



@click.command(name='run-arcana-pipeline',
               help="""Defines a new dataset, applies and launches a pipeline
in a single command. Given the complexity of combining all these steps in one
CLI, it isn't recommended for manual use, and is typically used when
deploying a pipeline within a container image.

Not all options are be used when defining datasets, however, the
'--dataset <NAME>' option can be provided to use an existing dataset
definition.

DATASET_ID_STR string containing the nickname of the data store, the ID of the
dataset (e.g. XNAT project ID or file-system directory) and the dataset's name
in the format <STORE-NICKNAME>//<DATASET-ID>:<DATASET-NAME>

PIPELINE_NAME is the name of the pipeline

WORKFLOW_LOCATION is the location to a Pydra workflow on the Python system path.
It can be omitted if PIPELINE_NAME matches an existing pipeline
""")
@click.argument("dataset_id_str")
@click.argument('pipeline_name')
@click.argument('workflow_location')
@click.option(
    '--parameter', '-p', nargs=2, default=(), metavar='<name> <value>', multiple=True, type=str,
    help=("free parameters of the workflow to be passed by the pipeline user"))
@click.option(
    '--input', nargs=4, default=(), metavar='<col-name> <col-format> <pydra-field> <format>',
    multiple=True, type=str,
    help=("link an input of the workflow to a column of the dataset, adding a source"
          "column matched by the name/path of the column if it isn't already present. "
          "Automatically generated source columns must be able to be specified by their "
          "path alone and be already in the format required by the workflow"))
@click.option(
    '--output', nargs=4, default=(), metavar='<col-name> <col-format> <pydra-field> <format>',
    multiple=True, type=str,
    help=("add a sink to the dataset and link it to an output of the workflow "
          "in a single step. The sink column be in the same format as produced "
          "by the workflow"))
@click.option(
    '--frequency', '-f', default=None, type=str,
    help=("the frequency of the nodes the pipeline will be executed over, i.e. "
          "will it be run once per-session, per-subject or per whole dataset, "
          "by default the highest frequency nodes (e.g. per-session)"))
@click.option(
    '--ids', default=None, type=str,
    help="List of IDs to restrict the pipeline to")
@click.option(
    '--work', '-w', 'work_dir', default=None,
    help=("The location of the directory where the working files "
          "created during the pipeline execution will be stored"))
@click.option(
    '--plugin', default='cf',
    help=("The Pydra plugin with which to process the workflow"))
@click.option(
    '--loglevel', type=str, default='info',
    help=("The level of detail logging information is presented"))
@click.option(
    '--dataset_hierarchy', type=str, default=None,
    help="Comma-separated hierarchy")
@click.option(
    '--dataset_space', type=str, default=None,
    help="The data space of the dataset")
@click.option(
    '--dataset_name', type=str, default=None,
    help="The name of the dataset")
@click.option(
    '--overwrite/--no-overwrite', type=bool,
    help=("Whether to overwrite the saved pipeline with the same name, if present"))
@click.option(
    '--configuration', nargs=2, default=(), metavar='<name> <value>', multiple=True, type=str,
    help=("configuration args of the workflow. Differ from parameters in that they is passed to the "
          "workflow at initialisation (and can therefore help specify its inputs) not as inputs. Values "
          "can be any valid JSON (including basic types)."))
def run_pipeline(dataset_id_str, pipeline_name, workflow_location, parameter,
                 input, output, frequency, overwrite, work_dir, plugin, loglevel,
                 dataset_name, dataset_space, dataset_hierarchy, ids, configuration):

    logging.basicConfig(level=getattr(logging, loglevel.upper()))

    if work_dir is None:
        work_dir = tempfile.mkdtemp()
    work_dir = Path(work_dir)

    store_cache_dir = work_dir / 'store-cache'
    pipeline_cache_dir = work_dir / 'pipeline-cache'

    try:
        dataset = Dataset.load(dataset_id_str)
    except KeyError:
        
        store_name, id, name = Dataset.parse_id_str(dataset_id_str)

        if dataset_name is not None:
            if name is not None:
                raise RuntimeError(
                    f"Dataset name specified in ID string {name} and "
                    f"'--dataset_name', {dataset_name}")
            name = dataset_name

        if dataset_hierarchy is None or dataset_space is None:
            raise RuntimeError(
                f"If the dataset ID string ('{dataset_id_str}') doesn't "
                "reference an existing dataset '--dataset_hierarchy' and "
                "'--dataset_space' must be provided")

        store = DataStore.load(store_name, cache_dir=store_cache_dir)   
        space = resolve_class(dataset_space, ['arcana.data.spaces'])
        hierarchy = dataset_hierarchy.split(',')
    
        dataset = store.new_dataset(
            id,
            hierarchy=hierarchy,
            space=space)

    pipeline_inputs = []
    for col_name, col_format_name, pydra_field, format_name in input:
        col_format = resolve_class(col_format_name, prefixes=['arcana.data.formats'])
        format = resolve_class(format_name, prefixes=['arcana.data.formats'])
        pipeline_inputs.append(PipelineInput(col_name, pydra_field, format))
        if col_name in dataset.columns:
            column = dataset[col_name]
            logger.info(f"Found existing source column {column}")
        else:
            logger.info(f"Adding new source column '{col_name}'")
            dataset.add_source(name=col_name, format=col_format)
            
    logger.info("Pipeline inputs: %s", pipeline_inputs)

    pipeline_outputs = []
    for col_name, col_format_name, pydra_field, format_name in output:
        format = resolve_class(format_name, prefixes=['arcana.data.formats'])
        col_format = resolve_class(col_format_name, prefixes=['arcana.data.formats'])
        pipeline_outputs.append(PipelineOutput(col_name, pydra_field, format))
        if col_name in dataset.columns:
            column = dataset[col_name]
            if not column.is_sink:
                raise ArcanaError(
                    "Output column name '{col_name}' shadows existing source column")
            logger.info(f"Found existing sink column {column}")
        else:
            logger.info(f"Adding new source column '{col_name}'")
            dataset.add_sink(name=col_name, format=col_format)

    logger.info("Pipeline outputs: %s", pipeline_outputs)

    workflow = resolve_class(workflow_location)(
        name='workflow',
        **{n: parse_value(v) for n, v in configuration})

    for pname, pval in parameter:
        if pval != '':
            setattr(workflow.inputs, pname, parse_value(pval))

    if pipeline_name in dataset.pipelines and not overwrite:
        pipeline = dataset.pipelines[pipeline_name]
        if workflow != pipeline.workflow:
            raise RuntimeError(
                f"A pipeline named '{pipeline_name}' has already been applied to "
                "which differs from one specified. Please use '--overwrite' option "
                "if this is intentional")
    else:
        pipeline = dataset.apply_pipeline(
            pipeline_name, workflow, inputs=pipeline_inputs,
            outputs=pipeline_outputs, frequency=frequency, overwrite=overwrite)

    # Instantiate the Pydra workflow
    workflow = pipeline(cache_dir=pipeline_cache_dir, plugin=plugin)
    
    if ids is not None:
        ids = ids.split(',')

    # execute the workflow    
    result = workflow(ids=ids)

    logger.info("Pipeline %s ran successfully for the following nodes\n: %s",
                pipeline_name, '\n'.join(result.output.processed))
    