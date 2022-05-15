from pathlib import Path
import yaml
from arcana import __version__


def create_doc(spec, doc_dir, pkg_name, src_file, flatten: bool):
    header = {
        "title": pkg_name,
        "weight": 10,
        "source_file": str(src_file),
    }

    if flatten:
        out_dir = doc_dir
    else:
        assert isinstance(doc_dir, Path)

        out_dir = doc_dir.joinpath(spec['_relative_dir'])

        assert doc_dir in out_dir.parents or out_dir == doc_dir

        out_dir.mkdir(parents=True, exist_ok=True)

    with open(f"{out_dir}/{pkg_name}.md", "w") as f:
        f.write("---\n")
        yaml.dump(header, f)
        f.write("\n---\n\n")

        f.write("## Package Info\n")
        tbl_info = MarkdownTable(f, "Key", "Value")
        if spec.get("version", None):
            tbl_info.write_row("Version", spec["version"])
        if spec.get("pkg_version", None):
            tbl_info.write_row("App version", spec["pkg_version"])
        if spec.get("wrapper_version", None):
            tbl_info.write_row("XNAT wrapper version", str(spec["wrapper_version"]))
        # if task.image and task.image != ':':
        #     tbl_info.write_row("Image", escaped_md(task.image))
        if spec.get("base_image", None):  # and task.image != spec["base_image"]:
            tbl_info.write_row("Base image", escaped_md(spec["base_image"]))
        if spec.get("maintainer", None):
            tbl_info.write_row("Maintainer", spec["maintainer"])
        if spec.get("info_url", None):
            tbl_info.write_row("Info URL", spec["info_url"])

        f.write("\n")
        
        f.write('## Commands\n')
        
        for cmd in spec['commands']:

            f.write(f"### {cmd['name']}\n")

            f.write(f'{cmd["description"]}\n\n')
            
            if spec.get("frequency", None):
                f.write(f'\nOperates on: {cmd["frequency"]}\n\n')

            # for x in task.inputs:
            f.write("#### Inputs\n")
            tbl_inputs = MarkdownTable(f, "Path", "Input format", "Stored format")
            if cmd.get('inputs'):
                for inpt in cmd['inputs']:
                    tbl_inputs.write_row(escaped_md(inpt['path']), escaped_md(inpt['format']), escaped_md(inpt.get('stored_format', 'format')))
                f.write("\n")

            f.write("#### Outputs\n")
            tbl_outputs = MarkdownTable(f, "Name", "Output format", "Stored format")
            if cmd.get('outputs'):
                # for x in task.outputs:
                for outpt in cmd.get('outputs', []):
                    tbl_outputs.write_row(escaped_md(outpt['path']), escaped_md(outpt['format']), escaped_md(outpt.get('stored_format', 'format')))
                f.write("\n")

            f.write("#### Parameters\n")
            tbl_params = MarkdownTable(f, "Name", "Data type")
            if cmd.get('parameters'):
                for param in cmd.get("parameters", []):
                    tbl_params.write_row(param['name'], param['format'])
                f.write("\n")


def escaped_md(value: str) -> str:
    if not value:
        return ""
    return f"`{value}`"


class MarkdownTable:
    def __init__(self, f, *headers: str) -> None:
        self.headers = tuple(headers)

        self.f = f
        self._write_header()

    def _write_header(self):
        self.write_row(*self.headers)
        self.write_row(*("-" * len(x) for x in self.headers))

    def write_row(self, *cols: str):
        cols = list(cols)
        if len(cols) > len(self.headers):
            raise ValueError(
                f"More entries in row ({len(cols)} than columns ({len(self.headers)})")

        # pad empty column entries if there's not enough
        cols += [""] * (len(self.headers) - len(cols))

        # TODO handle new lines in col
        self.f.write("|" + "|".join(col.replace("|", "\\|") for col in cols) + "|\n")