import argparse
import os
import re
from abc import ABC
from typing import List, Callable

from requests import Response
from requests.exceptions import ConnectionError
from requests_futures.sessions import FuturesSession

from html.parser import HTMLParser


class CodeParser(HTMLParser, ABC):
    def __init__(self, startline: int, endline: int) -> None:
        super().__init__()
        self.code = ''
        self._lines = [f'LC{line}' for line in range(startline, endline)]
        self._locked = True

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        if tag == 'td' and attrs[0][1] in self._lines:
            self._locked = False

    def handle_endtag(self, tag: str) -> None:
        if tag == 'td' and not self._locked:
            self._locked = True
            self.code += '\n'

    def handle_data(self, data: str) -> None:
        if not self._locked:
            self.code += data


class Validator:
    def __init__(self, targets: List, recursive=False) -> None:
        self._target_files = targets if not recursive else self._get_target_files(targets)
        self._recursive = recursive
        self._links = []
        self._errors = False
        self._code_mismatch = False

    @staticmethod
    def _get_target_files(targets: List[str]) -> List[str]:
        return [os.path.join(dirpath, file)
                for target in targets
                for dirpath, _, filenames in os.walk(target)
                for file in filenames if file.lower().endswith('.md')]

    def parse_links(self) -> None:
        link_regex = re.compile(
            r'!?\[(?:\[[^\[\]]*\]|\\[\[\]]?|`[^`]*`|[^\[\]\\])*?\]'
            r'\((http[s]?://(?:[a-zA-Z]|[0-9]|[#-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)\)'
        )
        code_link_regex = re.compile(
            r'\[_metadata_:link\]:'
            r'\s*(http[s]?://(?:[a-zA-Z]|[0-9]|[#-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)\n+'
            r'```\S*\n([\s\S]*?)```'
        )

        for path in self._target_files:
            with open(path) as file:
                try:
                    content = file.read()
                except UnicodeDecodeError:
                    continue
                code_links = code_link_regex.findall(content)
                links = link_regex.findall(content)
                self._links += \
                    [{
                        'url': url,
                        'code': code,
                        'file': path
                    } for url, code in code_links] + \
                    [{
                        'url': url,
                        'file': path
                    } for url in links]

    def _validate_link(self, link: dict) -> Callable:
        def response_hook(response: Response, *args, **kwargs) -> None:
            url = link['url']
            file = link['file']
            if response.status_code != 200:
                print(f'{file}: ERROR FETCHING THE FOLLOWING LINK ---> {url}')
                self._errors = True
            if 'code' in link.keys():
                code = link['code']
                lines_count = code.count('\n')
                first_line = int(url.split('#L')[1])
                parser = CodeParser(first_line, first_line + lines_count)
                parser.feed(response.text)
                if parser.code != code:
                    self._code_mismatch = True
                    sep = '-------------------------------------------------\n'
                    print(f'{file}: CODE IS OUTDATED\n')
                    print(f'Code in {file}:\n{sep}{code}{sep}')
                    print(f'Code on github:\n{sep}{parser.code}{sep}\n')
        return response_hook

    def validate(self) -> None:
        links_count = len(self._links)
        code_blocks_count = len([None for link in self._links if "code" in link.keys()])
        print(f'Found {links_count} links, {code_blocks_count} associated with code block\n\n')

        if not links_count:
            exit()

        print('Fetching...\n\n\n')
        with FuturesSession(max_workers=24) as session:
            futures = [(session.get(link['url'], hooks={
                'response': self._validate_link(link)
            }), link['url'], link['file'], 'code' in link.keys()) for link in self._links]

            for future, url, file, is_code_block in futures:
                try:
                    future.result()
                except ConnectionError:
                    self._errors = True
                    if is_code_block:
                        self._code_mismatch = True
                    print(f'{file}: ERROR FETCHING THE FOLLOWING LINK ---> {url}')

        if not self._errors and links_count:
            print('Every link returned 200 (:')
        if not self._code_mismatch and code_blocks_count:
            print('Every code block is up-to-date (:')


if __name__ == '__main__':
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "-r", "--recursive",
        help="validate all markdown files in the target directories recursively",
        action="store_true"
    )
    argparser.add_argument(
        'target',
        help='markdown file(s) or directories to validate',
        nargs='+'
    )
    arguments = argparser.parse_args()
    if any(map(os.path.isdir, arguments.target)) and not arguments.recursive:
        print('validate.py: cannot validate directory without -r flag')
        exit()
    elif any(map(os.path.isfile, arguments.target)) and arguments.recursive:
        print('validate.py: remove -r flag to validate files')
        exit()

    validator = Validator(targets=arguments.target, recursive=arguments.recursive)
    validator.parse_links()
    validator.validate()
