from setuptools import setup, find_packages
import os

def parse_requirements(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    return []

setup(
    name='dots_ocr',
    version='1.0',
    packages=find_packages(),
    include_package_data=True,
    install_requires=parse_requirements('requirements.txt'),  # now safe if missing
    description='dots.ocr: Multilingual Document Layout Parsing in one Vision-Language Model',
    url="https://github.com/rednote-hilab/dots.ocr",
    python_requires=">=3.10",
)
