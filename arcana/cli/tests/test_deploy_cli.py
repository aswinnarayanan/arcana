import yaml
from functools import reduce
from operator import mul
from arcana.cli.deploy import build, run_pipeline
from arcana.core.utils import class_location
from arcana.test.utils import show_cli_trace, make_dataset_id_str
from arcana.data.formats.common import Text


def test_deploy_build_cli(command_spec, cli_runner, work_dir):

    DOCKER_ORG = 'testorg'
    DOCKER_REGISTRY = 'test.registry.org'
    PKG_NAME = 'testpkg'

    concatenate_spec = {
        'commands': [command_spec],
        'pkg_version': '1.0',
        'wrapper_version': '1',
        'system_packages': [],
        'python_packages': [],
        'authors': ['some.one@an.email.org'],
        'info_url': 'http://concatenate.readthefakedocs.io',
        'test_config': True}

    build_dir = work_dir / 'build'
    build_dir.mkdir()
    spec_path = work_dir / 'test-specs'
    sub_dir = spec_path / PKG_NAME
    sub_dir.mkdir(parents=True)
    with open(sub_dir / 'concatenate.yml', 'w') as f:
        yaml.dump(concatenate_spec, f)

    result = cli_runner(build,
                        [str(spec_path), DOCKER_ORG,
                         '--build_dir', str(build_dir),
                         '--registry', DOCKER_REGISTRY,
                         '--loglevel', 'warning',
                         '--use-local-packages',
                         '--install_extras', 'test',
                         '--raise-errors'])
    assert result.exit_code == 0, show_cli_trace(result)
    assert result.output == f'{DOCKER_REGISTRY}/{DOCKER_ORG}/{PKG_NAME}.concatenate:1.0-1\n'


def test_run_pipeline_cli(concatenate_task, saved_dataset, cli_runner, work_dir):
    # Get CLI name for dataset (i.e. file system path prepended by 'file//')
    dataset_id_str = make_dataset_id_str(saved_dataset)
    bp = saved_dataset.blueprint
    duplicates = 1
    # Start generating the arguments for the CLI
    # Add source to loaded dataset
    result = cli_runner(
        run_pipeline,
        [dataset_id_str, 'a_pipeline', 'arcana.test.tasks:' + concatenate_task.__name__,
         '--input', 'file1', 'common:Text', 'in_file1', 'common:Text',
         '--input', 'file2', 'common:Text', 'in_file2', 'common:Text',
         '--output', 'concatenated', 'common:Text', 'out_file', 'common:Text',
         '--parameter', 'duplicates', str(duplicates),
         '--plugin', 'serial',
         '--work', str(work_dir),
         '--dataset_space', class_location(bp.space),
         '--dataset_hierarchy'] + [str(l) for l in bp.hierarchy])
    assert result.exit_code == 0, show_cli_trace(result)
    # Add source column to saved dataset
    sink = saved_dataset.add_sink('concatenated', Text)
    assert len(sink) == reduce(mul, saved_dataset.blueprint.dim_lengths)
    fnames = ['file1.txt', 'file2.txt']
    if concatenate_task.__name__.endswith('reverse'):
        fnames = [f[::-1] for f in fnames]
    expected_contents = '\n'.join(fnames * duplicates)
    for item in sink:
        item.get(assume_exists=True)
        with open(item.fs_path) as f:
            contents = f.read()
        assert contents == expected_contents