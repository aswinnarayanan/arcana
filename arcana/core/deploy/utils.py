
import typing as ty
from pathlib import Path
import json
import site
import pkg_resources
import os
from dataclasses import dataclass, field as dataclass_field
import yaml
from arcana import __version__
from arcana.exceptions import (ArcanaBuildError)
from arcana.exceptions import ArcanaError


@dataclass
class PipSpec():
    """Specification of a Python package"""

    name: str
    version: str = None
    url: str = None
    file_path: str = None
    extras: ty.List[str] = dataclass_field(default_factory=list)

    @classmethod
    def unique(cls, pip_specs: ty.Iterable):
        """Merge a list of Pip install specs so each package only appears once

        Parameters
        ----------
        pip_specs : ty.Iterable[PipSpec]
            the pip specs to merge

        Returns
        -------
        list[PipSpec]
            the merged pip specs

        Raises
        ------
        ArcanaError
            if there is a mismatch between two entries of the same package
        """
        dct = {}
        for pip_spec in pip_specs:
            if isinstance(pip_spec, dict):
                pkg_spec = PipSpec(**pkg_spec)
            try:
                prev_spec = dct[pip_spec.name]
            except KeyError:
                dct[pip_spec.name] = pip_spec
            else:
                if (prev_spec.version != pip_spec.version
                    or prev_spec.url != pip_spec.url
                        or prev_spec.file_path != pip_spec.file_path):
                    raise ArcanaError(
                        f"Cannot install '{pip_spec.name}' due to conflict "
                        f"between requested versions, {pip_spec} and {prev_spec}")
                prev_spec.extras.extend(pip_spec.extras)
        return list(dct.values())


def load_yaml_spec(path: Path, base_dir: Path=None):
    """Loads a deploy-build specification from a YAML file

    Parameters
    ----------
    path : Path
        path to the YAML file to load
    base_dir : Path
        path to the base directory of the suite of specs to be read

    Returns
    -------
    dict
        The loaded dictionary
    """
    def concat(loader, node):
        seq = loader.construct_sequence(node)
        return ''.join([str(i) for i in seq])

    def slice(loader, node):
        list, start, end = loader.construct_sequence(node)
        return list[start:end]

    def sliceeach(loader, node):
        _, start, end = loader.construct_sequence(node)
        return [
            loader.construct_sequence(x)[start:end] for x in node.value[0].value
        ]

    yaml.SafeLoader.add_constructor(tag='!join', constructor=concat)
    yaml.SafeLoader.add_constructor(tag='!concat', constructor=concat)
    yaml.SafeLoader.add_constructor(tag='!slice', constructor=slice)
    yaml.SafeLoader.add_constructor(tag='!sliceeach', constructor=sliceeach)

    with open(path, 'r') as f:
        data = yaml.load(f, Loader=yaml.SafeLoader)

    # frequency = data.get('frequency', None)
    # if frequency:
    #     # TODO: Handle other frequency types, are there any?
    #     data['frequency'] = Clinical[frequency.split('.')[-1]]

    data['_relative_dir'] = os.path.dirname(os.path.relpath(path, base_dir)) if base_dir else ''
    data['_module_name'] = os.path.basename(path).rsplit('.', maxsplit=1)[0]

    return data


def walk_spec_paths(spec_path: Path) -> ty.Iterable[Path]:
    """Walk a directory structure and return all YAML specs found with it

    Parameters
    ----------
    spec_path : Path
        path to the directory
    """
    if spec_path.is_file():
        yield spec_path
    else:
        for path in spec_path.rglob('*.yml'):
            yield path

def local_package_location(pip_spec: PipSpec):
    """Detect the installed locations of the packages, including development
    versions.

    Parameters
    ----------
    package: [PipSpec]
        the packages (or names of) the versions to detect

    Returns
    -------
    PipSpec
        the pip specification for the installation location of the package
    """
    
    if isinstance(pip_spec, str):
        parts = pip_spec.split('==')
        pip_spec = PipSpec(
            name=parts[0],
            version=(parts[1] if len(parts) == 2 else None))    
    try:
        pkg = next(p for p in pkg_resources.working_set
                    if p.project_name == pip_spec.name)
    except StopIteration:
        raise ArcanaBuildError(
            f"Did not find {pip_spec.name} in installed working set:\n"
            + "\n".join(sorted(
                p.key + '/' + p.project_name
                for p in pkg_resources.working_set)))
    if (pip_spec.version
            and not (pkg.version.endswith('.dirty') or pip_spec.version.endswith('.dirty'))
            and pkg.version != pip_spec.version):
        raise ArcanaBuildError(
            f"Requested package {pip_spec.version} does not match installed "
            f"{pkg.version}")
    pkg_loc = Path(pkg.location).resolve()
    # Determine whether installed version of requirement is locally
    # installed (and therefore needs to be copied into image) or can
    # be just downloaded from PyPI
    if pkg_loc not in site_pkg_locs:
        # Copy package into Docker image and instruct pip to install from
        # that copy
        pip_spec = PipSpec(name=pip_spec.name,
                           file_path=pkg_loc,
                           extras=pip_spec.extras)
    else:
        # Check to see whether package is installed via "direct URL" instead
        # of through PyPI
        direct_url_path = Path(pkg.egg_info) / 'direct_url.json'
        if direct_url_path.exists():
            with open(direct_url_path) as f:
                url_spec = json.load(f)
            url = url_spec['url']
            if 'vcs' in url_spec:
                url = url_spec['vcs'] + '+' + url
            if 'commit_id' in url_spec:
                url += '@' + url_spec['commit_id']
            pip_spec = PipSpec(name=pip_spec.name,
                               url=url,
                           extras=pip_spec.extras)
        else:
            pip_spec = PipSpec(name=pip_spec.name,
                               version=pkg.version,
                               extras=pip_spec.extras)
    return pip_spec



DOCKER_HUB = 'https://index.docker.io/v1/'
site_pkg_locs = [Path(p).resolve() for p in site.getsitepackages()]