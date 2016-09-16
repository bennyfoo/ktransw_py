#
# Copyright (c) 2016, G.A. vd. Hoorn
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from __future__ import print_function

import os
import sys
import argparse
import subprocess
import logging
import re


def main():
    KTRANSW_VERSION='0.1.99'
    KTRANS_BIN_NAME='ktrans.exe'
    GPP_BIN_NAME='gpp.exe'
    _OS_EX_DATAERR=65
    KL_SUFFIX = '.kl'
    PCODE_SUFFIX = '.pc'

    description=("Version {0}\n\n"
        "A wrapper around Fanuc Robotics' command-line Karel translator ({1})\n"
        "that adds a C-like preprocessor, support for multiple include directories,\n"
        "conditional compilation, include guards and macros."
            .format(KTRANSW_VERSION, KTRANS_BIN_NAME))

    epilog=("Example invocation:\n\n  ktransw /IC:\\foo\\bar\\include "
        "/IC:\\baz\\include C:\\my_prog.kl /config robot.ini\n\nAll arguments "
        "using forward-slash notation (except '/I') are passed on\nto ktrans.")

    parser = argparse.ArgumentParser(prog='ktransw', description=description,
        epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('-v', '--verbose', action='store_true', dest='verbose',
        help='Print (lots of) debug information')
    parser.add_argument('-q', '--quiet', action='store_true', dest='quiet',
        help='Print nothing, except when ktrans encounters an error')
    parser.add_argument('-d', '--dry-run', action='store_true', dest='dry_run',
        help='Do nothing, except checking parameters')

    parser.add_argument('-M', action='store_true', dest='dep_output',
        help='Output GCC compatible dependency file')
    parser.add_argument('-MM', action='store_true', dest='ignore_syshdrs',
        help="Like '-M', but don't include system headers")
    parser.add_argument('-MT', type=str, dest='dep_target', metavar='target',
        help="Change the target of the rule emitted by dependency generation "
            "(default: base name of source, with object extension (.pc))")
    parser.add_argument('-MF', type=str, dest='dep_fname', metavar='file',
        help="When used with -M or -MM, specifies a file to write the "
            "dependencies to.")
    parser.add_argument('-MG', action='store_true', dest='ignore_missing_hdrs',
        help="Assume missing header files are generated files and add them "
            "to the dependency list without raising an error")
    parser.add_argument('-MP', action='store_true', dest='add_phony_tgt_for_deps',
        help="Add a phony target for each dependency to support renaming "
            "dependencies without having to update the Makefile to match")

    parser.add_argument('--ktrans', type=str, dest='ktrans_path', metavar='PATH',
        help="Location of ktrans (by default ktransw assumes it's on the "
            "Windows PATH)")
    parser.add_argument('--gpp', type=str, dest='gpp_path', metavar='PATH',
        help="Location of gpp (by default ktransw assumes it's on the "
            "Windows PATH)")
    parser.add_argument('-I', action='append', type=str, dest='include_dirs',
        metavar='PATH', default=[], help='Include paths (multiple allowed)')
    parser.add_argument('ktrans_args', type=str, nargs='*', metavar='ARG',
        help="Arguments to pass on to ktrans. Use normal (forward-slash) "
        "notation here")

    # support forward-slash arg notation for include dirs
    for i in range(1, len(sys.argv)):
        if sys.argv[i].startswith('/I'):
            sys.argv[i] = sys.argv[i].replace('/I', '-I', 1)
    args = parser.parse_args()

    # configure the logger
    FMT='%(levelname)-8s | %(message)s'
    logging.basicConfig(format=FMT, level=logging.INFO)
    logger = logging.getLogger('ktransw')
    if args.verbose:
        logger.setLevel(logging.DEBUG)


    logger.debug("Ktrans Wrapper v{0}".format(KTRANSW_VERSION))


    # we expect ktrans to be on the path. If it's not, user should have
    # provided an alternative location
    ktrans_path = os.path.abspath(args.ktrans_path) if args.ktrans_path else KTRANS_BIN_NAME
    logger.debug("Setting ktrans path to: {0}".format(ktrans_path))

    # we expect gpp to be on the path. If it's not, user should have
    # provided an alternative location
    gpp_path = os.path.abspath(args.gpp_path) if args.gpp_path else GPP_BIN_NAME
    logger.debug("Setting gpp path to: {0}".format(gpp_path))


    # bit of a kludge, but we assume:
    #
    #  1. ktrans args always start with a forward-slash
    #  2. things starting with a 'V' or 'v' are core version identifiers
    #  3. everything else is a (potentially relative) path
    #
    # everything in category 3 is made absolute.
    for i in range(0, len(args.ktrans_args)):
        if (args.ktrans_args[i][0] != '/') and (args.ktrans_args[i][0] != 'V') and (args.ktrans_args[i][0] != 'v'):
            args.ktrans_args[i] = os.path.abspath(args.ktrans_args[i])

    logger.debug("Parsed args:")
    for key, val in vars(args).iteritems():
        if type(val) == list:
            logger.debug("  {0}:".format(key))
            for item in val:
                logger.debug("    {0}".format(item))
        else:
            logger.debug("  {0}: {1}".format(key, val))


    # extract args which refer to KAREL sources: we can just search for
    # arguments with '.kl' in it, as ktrans only considers files with that
    # extension.
    kl_files = [arg for arg in args.ktrans_args if arg.endswith(KL_SUFFIX)]


    # see if we just need to output dependency info
    if (args.dep_output or args.ignore_syshdrs) and (len(kl_files) > 0):
        # assume there's only one input source file (or: we ignore all others)
        kl_file = kl_files[0]
        logger.debug("Dependency output for {0}".format(kl_file))

        incs = get_includes_from_file(kl_file)
        logger.debug("Found {0} includes".format(len(incs)))

        # make sure everything ends in the right suffix
        # XXX: no longer needed with gpp
        #for i in range(0, len(incs)):
        #    if not incs[i].endswith(KL_SUFFIX):
        #        incs[i] = incs[i] + KL_SUFFIX

        # target name we use is 'base source file name + .pc', OR the name
        # provided as a command line arg
        target = args.dep_target or os.path.basename(os.path.splitext(kl_file)[0]) + PCODE_SUFFIX

        # resolve all relative includes to their respective include directories
        deps = []
        for hdr in incs:
            if args.ignore_syshdrs and is_system_header(hdr):
                logger.debug("Ignoring system header '{0}'".format(hdr))
                continue

            # all non-absolute paths are headers we need to find first
            hdr_path = hdr
            if not os.path.isabs(hdr_path):
                try:
                    hdr_dir = find_hdr_in_incdirs(hdr_path, args.include_dirs)

                    # make relative header absolute by prefixing it with the
                    # location we found it in
                    hdr_path = os.path.join(hdr_dir, hdr_path)
                    logger.debug("Found {0} in '{1}'".format(hdr, hdr_dir))

                except ValueError, e:
                    if not args.ignore_missing_hdrs:
                        # we were not asked to ignore this, so exit with an error
                        sys.stderr.write("ktransw: fatal error: {0}: No such file or directory\n".format(hdr))
                        sys.exit(_OS_EX_DATAERR)

            logger.debug("Adding {0} to dependencies".format(hdr_path))
            deps.append(hdr_path)

        dep_lines = "{0} : {1}\n".format(target, ' \\\n\t'.join([dep for dep in deps]))

        if args.add_phony_tgt_for_deps:
            dep_lines += '\n'.join(['{0}:'.format(dep) for dep in deps]) + '\n'

        # write out dependency file
        if args.dep_fname:
            with open(args.dep_fname, 'w') as outf:
                outf.write(dep_lines)
        # or to stdout
        else:
            sys.stdout.write(dep_lines)

        # done
        sys.exit(0)


    # avoid running a build if we don't need it
    needs_build = len(kl_files) > 0
    logger.debug("{0} a build".format("Needs" if needs_build else "Doesn't need"))

    if not needs_build:
        ktrans_cmdline = [ktrans_path]
        ktrans_cmdline.extend(args.ktrans_args)

        sys.stdout.write("KTRANSW V{}, Copyright (C) 2016 G.A. vd. Hoorn\n"
            .format(KTRANSW_VERSION))
        ktrans_ret = subprocess.call(ktrans_cmdline)

        logger.debug("End of ktrans, ret: {0}".format(ktrans_ret))
        sys.exit(ktrans_ret)

    # assume there's only one input source file (or: we ignore all others)
    kl_file = kl_files[0]

    # checks done, can now proceed to actual pre-processing / translation ..
    # .. but only if not requested to do a dry-run
    if args.dry_run:
        logger.debug("Not calling ktrans or gpp: dry run requested")
        sys.exit(0)

    # create temporary directory to store preprocessed file in. We
    # avoid problems with temporary files (via NamedTemporaryFile fi) being
    # not readable by other processes in this way.
    with TemporaryDirectory(prefix='ktransw-', suffix='-buildd') as dname:
        # unfortunately we need to create a temporary file to store the
        # preprocessed KAREL source in, as ktrans doesn't support reading
        # from stdin.

        # TODO: see if ktrans will read from a named pipe ('\\.\pipe\temp.kl')

        fname = os.path.join(dname, os.path.basename(kl_file))
        logger.debug("Storing preprocessed KAREL source at: {}".format(fname))


        # do actual pre-processing
        logger.debug("Starting pre-processing of {}".format(kl_file))

        # setup command line for gpp
        gpp_cmdline = setup_gpp_cline(gpp_path, kl_file, fname, args.include_dirs)
        # TODO: why do we need to do this ourselves? gpp doesn't run
        #       correctly if we don't, but it shouldn't matter?
        gpp_cmdline = ' '.join(gpp_cmdline)

        # invoke gpp and save output
        logger.debug("Starting gpp as: '{0}'".format(gpp_cmdline))
        gpp_proc = subprocess.Popen(gpp_cmdline, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        (pstdout, pstderr) = gpp_proc.communicate()

        logger.debug("End of gpp, ret: {0}".format(gpp_proc.returncode))

        # make sure to relay errors in case there are any, even if we're quiet
        if (gpp_proc.returncode != 0):
            sys.stderr.write(
                "{}\n"
                "Translation terminated\n".format(pstderr))

            # TODO: this is not very nice, as it essentially merges the set of
            # possible exit codes of gpp with those of ktrans (and gpp's are
            # positive, while ktrans' are negative ..)
            sys.exit(gpp_proc.returncode)


        # pre-processing done, get ktrans to translate the intermediary file


        # replace user specified source file with the preprocessed one
        for i in range(0, len(args.ktrans_args)):
            if (args.ktrans_args[i] == kl_file):
                args.ktrans_args[i] = fname

        # setup ktrans command line args
        ktrans_cmdline = [ktrans_path]
        ktrans_cmdline.extend(args.ktrans_args)
        ktrans_cmdline = ' '.join(ktrans_cmdline)

        logger.debug("Starting ktrans as: '{}'".format(ktrans_cmdline))
        # NOTE: we remap stderr to stdout as ktrans doesn't use those
        # consistently (ie: uses stderr when it should use stdout and
        # vice versa)
        process = subprocess.Popen(ktrans_cmdline, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        (pstdout, _) = process.communicate()

        # let caller know how we did
        logger.debug("End of ktrans, ret: {0}".format(process.returncode))

        # print ktrans output only on error or if we're not quiet
        if (process.returncode != 0) or (not args.quiet) or args.verbose:
            # TODO: we loose stdout/stderr interleaving here
            # TODO: the error messages refer to lines in the temporary,
            # preprocessed KAREL source file, not the original one.
            sys.stdout.write(pstdout.replace(dname, os.path.dirname(kl_file)) + '\n')

        logger.debug("End of ktrans, ret: {0}".format(process.returncode))
        sys.exit(process.returncode)


def get_includes_from_file(fname):
    with open(fname, 'rb') as fd:
        source = fd.read()
        return scan_for_inc_stmts(source)


def scan_for_inc_stmts(text):
    matches = re.findall(r'^(?!\s*--)\s*%INCLUDE\s+(\S+).*$', text, re.MULTILINE)
    return matches or []


def is_system_header(header):
    # TODO: this is only a list of 'system headers' for V7.70-1
    return header in [
        "iosetup.kl",
        "kldctptx.kl",
        "kldcutil.kl",
        "klersys.kl",
        "klerxmlf.kl",
        "klevaxdf.kl",
        "klevccdf.kl",
        "klevkeys.kl",
        "klevkmsk.kl",
        "klevksp.kl",
        "klevtpe.kl",
        "klevutil.kl",
        "kliosop.kl",
        "kliotyps.kl",
        "kliouop.kl",
        "klrdread.kl",
        "klrdutil.kl",
        "kluifdir.kl",
        "passcons.kl",
        "ppedef.kl",
        "runform.kl",
        "sledef.kl"
    ]


def find_hdr_in_incdirs(header, include_dirs):
    for include_dir in include_dirs:
        if os.path.exists(os.path.join(include_dir, header)):
            return include_dir
    raise ValueError()


def setup_gpp_cline(gpp_exe, src_file, dest_file, include_dirs):
    # setup gpp command line (based on 'C++ compatibility mode', but with some
    # changes to better integrate -- style-wise -- with Karel sources)

    # TODO: see if we can restore bw-compat with plain ktrans by setting the
    # 'macro start sequence' to '\n--#\w' or something similar (a KAREL
    # comment), and by pre-processing (with ktransw) all includes to add
    # the '.kl' extension that ktrans expects (although that does make it
    # impossible to use alternative file extensions)
    #
    # Maybe make it an option? ie: --ktrans-bw

    gpp_cmdline = [
        gpp_exe,

        '+z',       # Set text mode to Unix mode (LF terminator)

        '-U',       # User-defined mode
        '""',       # the macro start sequence
        '""',       # the macro end sequence for a call without arguments
        '"("',      # the argument start sequence
        '","',      # the argument separator
        '")"',      # the argument end sequence
        '"("',      # the list of characters to stack for argument balancing
        '")"',      # the list of characters to unstack
        '"#"',      # the string to be used for referring to an argument by number
        '""',       # and finally the quote character (escapes embedded string chars)

        '-M',       # User-defined mode specifications for meta-macros
        '"\\n%\w"', # the macro start sequence
        '"\\n"',    # the macro end sequence for a call without arguments
        '" "',      # the argument start sequence
        '" "',      # the argument separator
        '"\\n"',    # the argument end sequence
        '""',       # the list of characters to stack for argument balancing
        '""',       # and the list of characters to unstack

        # TODO: somehow line endings get screwed up with this
        #'+c',       # Specify comments
        #'"--"',     # the beginning of a comment
        #'"\\n"',    # end of comment

        # TODO: somehow line endings get screwed up with this
        #'+s',       # Specify strings
        #'"\'"',     # the beginning of a string
        #'"\'"',     # the end of a string
        #'""'        # string-quote character (escapes embedded string chars)
    ]

    # append include dirs we got from caller
    gpp_cmdline.extend(['-I' + d for d in include_dirs])

    # make gpp output to temporary file immediately, so we can have
    # ktrans open that, instead of having to write to the intermediary file
    # ourselves
    gpp_cmdline.extend(['-o', dest_file])

    # finally: the input to gpp is the KAREL file that we are supposed
    # to be compiling
    gpp_cmdline.extend([src_file])

    return gpp_cmdline


class TemporaryDirectory(object):
    # http://stackoverflow.com/a/19299884
    def __init__(self, suffix="", prefix="tmp", dir=None):
        from tempfile import mkdtemp
        self._closed = False
        self.name = None
        self.name = mkdtemp(suffix, prefix, dir)

    def __repr__(self):
        return "<{} {!r}>".format(self.__class__.__name__, self.name)

    def __enter__(self):
        return self.name

    def cleanup(self, _warn=False):
        if self.name and not self._closed:
            try:
                self._rmtree(self.name)
            except (TypeError, AttributeError) as ex:
                if "None" not in str(ex):
                    raise
                print("ERROR: {!r} while cleaning up {!r}".format(ex, self,),
                      file=_sys.stderr)
                return
            self._closed = True
            if _warn:
                self._warn("Implicitly cleaning up {!r}".format(self),
                           ResourceWarning)

    def __exit__(self, exc, value, tb):
        self.cleanup()

    def __del__(self):
        self.cleanup(_warn=True)

    _listdir = staticmethod(os.listdir)
    _path_join = staticmethod(os.path.join)
    _isdir = staticmethod(os.path.isdir)
    _islink = staticmethod(os.path.islink)
    _remove = staticmethod(os.remove)
    _rmdir = staticmethod(os.rmdir)
    import warnings as _warnings
    _warn = _warnings.warn

    def _rmtree(self, path):
        for name in self._listdir(path):
            fullname = self._path_join(path, name)
            try:
                isdir = self._isdir(fullname) and not self._islink(fullname)
            except OSError:
                isdir = False
            if isdir:
                self._rmtree(fullname)
            else:
                try:
                    self._remove(fullname)
                except OSError:
                    pass
        try:
            self._rmdir(path)
        except OSError:
            pass


if __name__ == '__main__':
    main()
