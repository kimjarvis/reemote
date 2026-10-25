"""Simulated hosts for the POC.

The model is restricted to generating commands: there is no SSH and no
async. A ``SimulatedHost`` stands in for a real machine and holds the
state that facts read (installed packages, filesystem links). The driver
mutates this state via ``send_command`` when an operation is changed,
which is what makes the "fresh fact cache per lockstep step" property
observable in the demo and tests.
"""

from dataclasses import dataclass, field


@dataclass
class SimulatedHost:
    name: str
    packages: set[str] = field(default_factory=set)
    links: dict[str, str] = field(default_factory=dict)  # src -> target
    # Side effects of installing a package: package -> {link src: target}.
    # This is what lets the demo reproduce the design doc section 9 trace:
    # installing nginx creates the default-site link, which the *next*
    # operation then sees because the fact cache is recreated per step.
    installs_links: dict[str, dict[str, str]] = field(default_factory=dict)

    # State mutations, invoked only by the driver's send_command.

    def install_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            if package in self.packages:
                continue
            self.packages.add(package)
            for src, target in self.installs_links.get(package, {}).items():
                self.links[src] = target

    def remove_packages(self, packages: tuple[str, ...]) -> None:
        self.packages.difference_update(packages)

    def make_link(self, src: str, target: str) -> None:
        self.links[src] = target

    def remove_link(self, src: str) -> None:
        self.links.pop(src, None)

    def __str__(self) -> str:
        return self.name


__all__ = ["SimulatedHost"]
