"""Remove the build clause from the known Compose file in an offline bundle."""
import pathlib
import sys


def offline_compose(text):
    block = '    build:\n      context: .\n      dockerfile: docker/Dockerfile\n'
    if text.count(block) != 1:
        raise ValueError('Compose build block changed; update bundle generation explicitly')
    return text.replace(block, '')


if __name__ == '__main__':
    path = pathlib.Path(sys.argv[1])
    path.write_text(offline_compose(path.read_text()))
