import datetime
import pathlib
import subprocess
import sys
import textwrap

import pkg_resources
import click


@click.command()
@click.option("--dist", "--distribution", "-d", "distribution", required=True)
@click.option("--root", "-r", default=None)
@click.option("--out", "-o", type=click.File("w"), default=sys.stdout)
def cli(distribution, root, out):
    version = pkg_resources.get_distribution(distribution).version

    sha = subprocess.check_output(
        [
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            "HEAD",
        ],
        cwd=root,
        encoding="utf-8",
    ).strip()

    actual_root_path = subprocess.check_output(
        [
            "git",
            "rev-parse",
            "--show-toplevel",
        ],
        cwd=root,
        encoding="utf-8",
    ).strip()

    file_path = pathlib.Path(__file__).relative_to(actual_root_path)

    contents = textwrap.dedent(
        f"""\
    # This file was automatically generated by {file_path}
    __version_build_time__ = '{datetime.datetime.utcnow().isoformat()}'
    
    __version__ = '{version}'
    __sha__ = '{sha}'
    __revision__ = '{sha}'
    """
    )

    out.write(contents)
