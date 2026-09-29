from setuptools import find_packages, setup

setup(
    name="private-agent-sidecar",
    version="0.2.0",
    packages=find_packages(),
    install_requires=["cryptography>=42,<51"],
    extras_require={"deepseek": ["torch", "transformers", "peft", "safetensors"]},
    entry_points={"console_scripts": ["agent-sidecar=agent_sidecar.cli:main"]},
)
