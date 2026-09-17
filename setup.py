"""Package the canonical repository mapping rules without duplicate source files."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithMapping(build_py):
    def run(self) -> None:
        super().run()
        source = Path(__file__).parent / "mapping"
        target = Path(self.build_lib) / "step2aas" / "mapping"
        self.mkpath(str(target))
        for rule in sorted(source.glob("*.yaml")):
            self.copy_file(str(rule), str(target / rule.name))


setup(cmdclass={"build_py": BuildPyWithMapping})
