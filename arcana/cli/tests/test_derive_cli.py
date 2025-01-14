from functools import reduce
from operator import mul
from arcana.cli.apply import apply_pipeline
from arcana.cli.derive import derive_column
from arcana.test.utils import show_cli_trace, make_dataset_id_str
from arcana.data.formats.common import Text


def test_derive_cli(saved_dataset, concatenate_task, cli_runner):
    # Get CLI name for dataset (i.e. file system path prepended by 'file//')
    dataset_id_str = make_dataset_id_str(saved_dataset)
    bp = saved_dataset.__annotations__["blueprint"]
    duplicates = 3
    # Start generating the arguments for the CLI
    # Add source to loaded dataset
    result = cli_runner(
        apply_pipeline,
        [
            dataset_id_str,
            "a_pipeline",
            "arcana.test.tasks:" + concatenate_task.__name__,
            "--source",
            "file1",
            "in_file1",
            "common:Text",
            "--source",
            "file2",
            "in_file2",
            "common:Text",
            "--sink",
            "concatenated",
            "out_file",
            "common:Text",
            "--parameter",
            "duplicates",
            str(duplicates),
        ],
    )
    assert result.exit_code == 0, show_cli_trace(result)
    # Add source column to saved dataset
    result = cli_runner(
        derive_column, [dataset_id_str, "concatenated", "--plugin", "serial"]
    )
    assert result.exit_code == 0, show_cli_trace(result)
    sink = saved_dataset.add_sink("concatenated", Text)
    assert len(sink) == reduce(mul, bp.dim_lengths)
    fnames = ["file1.txt", "file2.txt"]
    if concatenate_task.__name__.endswith("reverse"):
        fnames = [f[::-1] for f in fnames]
    expected_contents = "\n".join(fnames * duplicates)
    for item in sink:
        item.get(assume_exists=True)
        with open(item.fs_path) as f:
            contents = f.read()
        assert contents == expected_contents
