import curses.ascii
import datetime
import json
import logging
import pipes
import subprocess
import re
import tempfile
import uuid
import weakref

import six
from taskw import TaskWarriorShellout
from taskw.task import Task as TaskwTask


class TaskwarriorError(Exception):
    def __init__(self, stderr, stdout, code):
        self.stderr = stderr.strip()
        self.stdout = stdout.strip()
        self.code = code
        message = "%s: %s :: %s" % (
            self.code,
            self.stdout,
            self.stderr
        )
        super(TaskwarriorError, self).__init__(message)


class TaskwarriorClient(TaskWarriorShellout):
    def __init__(self, *args, **kwargs):
        self.store = None
        if 'store' in kwargs:
            self.store = weakref.proxy(kwargs.pop('store'))
        super(TaskwarriorClient, self).__init__(*args, marshal=True, **kwargs)

    def send_client_message(self, name, *args, **kwargs):
        if not self.store:
            return

        try:
            self.store.receive_client_message(name, *args, **kwargs)
        except weakref.ReferenceError:
            pass

    def _get_acceptable_properties(self):
        return TaskwTask.FIELDS.keys() + self.config.get_udas().keys()

    def _get_acceptable_prefix(self, command):
        if ' ' in command:
            return command
        if command.lower() in self._get_acceptable_properties():
            return command.lower()
        return False

    def _get_json(self, *args):
        encoded = self._execute(*args)[0]
        decoded = encoded.decode('utf-8', 'replace')
        return json.loads(decoded)

    def _strip_unsafe_args(self, *args):
        if not args:
            return args
        final_args = [args[0]]
        description_args = []
        for raw_arg in args[1:]:
            arg = self._strip_unsafe_chars(raw_arg)
            if ':' in arg:
                cmd, value = arg.split(':', 1)
                acceptable_cmd = self._get_acceptable_prefix(cmd)
                if acceptable_cmd:
                    final_args.append(
                        ':'.join(
                            [acceptable_cmd, value]
                        )
                    )
                else:
                    description_args.append(arg)
            elif arg.startswith('+') or arg.startswith('-'):
                final_args.append(arg)
            else:
                description_args.append(arg)

        return final_args + ['--'] + description_args

    def _strip_unsafe_chars(self, incoming):
        if isinstance(incoming, unicode):
            incoming = incoming.encode('utf8')
        return ''.join(
            char for char in incoming if curses.ascii.isprint(char)
        )

    def _execute_safe(self, *args):
        return self._execute(
            *self._strip_unsafe_args(*args)
        )

    def _get_logger(self, cmd):
        try:
            uuid.UUID(str(cmd[0]))
            command = str(cmd[1])
        except (ValueError, IndexError):
            command = str(cmd[0])
        return logging.getLogger('%s.%s' % (__name__, command))

    def _execute(self, *args):
        """ Execute a given taskwarrior command with arguments

        Returns a 2-tuple of stdout and stderr (respectively).

        """
        logger = self._get_logger(args)

        command = (
            [
                'task',
                'rc:%s' % self.config_filename,
            ]
            + self.get_configuration_override_args()
            + [six.text_type(arg) for arg in args]
        )

        # subprocess is expecting bytestrings only, so nuke unicode if present
        for i in range(len(command)):
            if isinstance(command[i], six.text_type):
                command[i] = command[i].encode('utf-8')

        started = datetime.datetime.now()

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = proc.communicate()

        total_seconds = (datetime.datetime.now() - started).total_seconds()

        self.send_client_message(
            'metadata',
            'taskwarrior.execute',
            {
                'command': ' '.join(
                    pipes.quote(c.decode('utf-8')) for c in command
                ),
                'arguments': ' '.join(
                    pipes.quote(six.text_type(arg)) for arg in args
                ),
                'stderr': stderr,
                'stdout': stdout,
                'returncode': proc.returncode,
                'duration': total_seconds,
            }
        )

        if proc.returncode != 0:
            logger.error(
                'Non-zero return code returned from taskwarrior: %s; %s' % (
                    proc.returncode,
                    stderr,
                ),
                extra={
                    'stack': True,
                    'data': {
                        'code': proc.returncode,
                        'command': command,
                        'stdout': stdout,
                        'stderr': stderr,
                    }
                }
            )
            raise TaskwarriorError(stderr, stdout, proc.returncode)

        logger.debug("%s: %s", command, stdout)
        return stdout, stderr

    def import_task(self, value):
        with tempfile.NamedTemporaryFile() as jsonout:
            jsonout.write(json.dumps(value))
            jsonout.flush()

            self._execute(
                'import',
                jsonout.name,
            )

            _, task = self.get_task(uuid=value['uuid'])

            return task
