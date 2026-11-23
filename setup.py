from setuptools import find_packages, setup

setup(
    name="private-agent-sidecar",
    version="0.3.0",
    packages=find_packages(include=["agent_sidecar", "agent_sidecar.*"]),
    install_requires=["cryptography>=42,<51"],
    entry_points={"console_scripts": ["agent-sidecar=agent_sidecar.cli:main"]},
)
