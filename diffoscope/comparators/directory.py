# -*- coding: utf-8 -*-
#
# diffoscope: in-depth comparison of files, archives, and directories
#
# Copyright © 2015 Jérémy Bobbio <lunar@debian.org>
#
# diffoscope is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# diffoscope is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with diffoscope.  If not, see <http://www.gnu.org/licenses/>.

from contextlib import contextmanager
import os
import os.path
import re
import subprocess
from diffoscope import logger, tool_required, RequiredToolNotFound
from diffoscope.difference import Difference
import diffoscope.comparators
from diffoscope.comparators.binary import FilesystemFile
from diffoscope.comparators.utils import Container, Command


def list_files(path):
    path = os.path.realpath(path)
    all_files = []
    for root, dirs, names in os.walk(path):
        all_files.extend([os.path.join(root[len(path) + 1:], dir) for dir in dirs])
        all_files.extend([os.path.join(root[len(path) + 1:], name) for name in names])
    all_files.sort()
    return all_files


class Stat(Command):
    @tool_required('stat')
    def cmdline(self):
        return ['stat', self.path]

    FILE_RE = re.compile(r'^\s*File:.*$')
    DEVICE_RE = re.compile(r'Device: [0-9a-f]+h/[0-9]+d')
    INODE_RE = re.compile(r'Inode: [0-9]+')
    LINKS_RE = re.compile(r'Links: [0-9]+')
    ACCESS_TIME_RE = re.compile(r'^Access: [0-9]{4}-[0-9]{2}-[0-9]{2}.*$')
    CHANGE_TIME_RE = re.compile(r'^Change: [0-9]{4}-[0-9]{2}-[0-9]{2}.*$')

    def filter(self, line):
        line = line.decode('utf-8')
        line = Stat.FILE_RE.sub('', line)
        line = Stat.DEVICE_RE.sub('', line)
        line = Stat.INODE_RE.sub('', line)
        line = Stat.LINKS_RE.sub('', line)
        line = Stat.ACCESS_TIME_RE.sub('', line)
        line = Stat.CHANGE_TIME_RE.sub('', line)
        return line.encode('utf-8')


@tool_required('lsattr')
def lsattr(path):
    try:
        output = subprocess.check_output(['lsattr', '-d', path], shell=False, stderr=subprocess.STDOUT).decode('utf-8')
        return output.split()[0]
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            # filesystem doesn't support xattrs
            return ''


class Getfacl(Command):
    @tool_required('getfacl')
    def cmdline(self):
        return ['getfacl', '-p', '-c', self.path]


def compare_meta(path1, path2):
    logger.debug('compare_meta(%s, %s)', path1, path2)
    differences = []
    try:
        differences.append(Difference.from_command(Stat, path1, path2))
    except RequiredToolNotFound:
        logger.warn("'stat' not found! Is PATH wrong?")
    try:
        lsattr1 = lsattr(path1)
        lsattr2 = lsattr(path2)
        differences.append(Difference.from_text(
                               lsattr1, lsattr2, path1, path2, source="lattr"))
    except RequiredToolNotFound:
        logger.info("Unable to find 'lsattr'.")
    try:
        differences.append(Difference.from_command(Getfacl, path1, path2))
    except RequiredToolNotFound:
        logger.info("Unable to find 'getfacl'.")
    return [d for d in differences if d is not None]


def compare_directories(path1, path2, source=None):
    return FilesystemDirectory(path1).compare(FilesystemDirectory(path2))


class Directory(object):
    @staticmethod
    def recognizes(file):
        return file.is_directory()


class FilesystemDirectory(object):
    def __init__(self, path):
        self._path = path

    @property
    def path(self):
        return self._path

    @property
    def name(self):
        return self._path

    def is_directory(self):
        return True

    def has_same_content_as(self, other):
        # no shortcut
        return False

    def compare(self, other, source=None):
        differences = []
        try:
            listing_diff = Difference.from_text('\n'.join(list_files(self.path)),
                                                '\n'.join(list_files(other.path)),
                                                self.path, other.path, source='file list')
            if listing_diff:
                differences.append(listing_diff)
        except RequiredToolNotFound:
            logger.info("Unable to find 'getfacl'.")
        differences.extend(compare_meta(self.name, other.name))
        my_container = DirectoryContainer(self)
        other_container = DirectoryContainer(other)
        my_names = my_container.get_member_names()
        other_names = other_container.get_member_names()
        for name in sorted(set(my_names).intersection(other_names)):
            my_file = my_container.get_member(name)
            other_file = other_container.get_member(name)
            inner_difference = diffoscope.comparators.compare_files(
                                   my_file, other_file, source=name)
            meta_differences = compare_meta(my_file.name, other_file.name)
            if meta_differences and not inner_difference:
                inner_difference = Difference(None, my_file.path, other_file.path)
            if inner_difference:
                inner_difference.add_details(meta_differences)
                differences.append(inner_difference)
        if not differences:
            return None
        difference = Difference(None, self.path, other.path, source)
        difference.add_details(differences)
        return difference


class DirectoryContainer(Container):
    def get_member_names(self):
        path = self.source.path
        names = []
        for root, _, files in os.walk(path):
            if root == path:
                root = ''
            else:
                root = root[len(path) + 1:]
            names.extend([os.path.join(root, f) for f in files])
        return names

    def get_member(self, member_name):
        return FilesystemFile(os.path.join(self.source.path, member_name))
