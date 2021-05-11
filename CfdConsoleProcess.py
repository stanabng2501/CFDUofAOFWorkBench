# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2017 Oliver Oxtoby (CSIR) <ooxtoby@csir.co.za>          *
# *   Copyright (c) 2017 Johan Heyns (CSIR) <jheyns@csir.co.za>             *
# *   Copyright (c) 2017 Alfred Bogaers (CSIR) <abogaers@csir.co.za>        *
# *   Copyright (c) 2019 Oliver Oxtoby <oliveroxtoby@gmail.com>             *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

from __future__ import print_function
import platform
import os
from PySide import QtCore
from PySide.QtCore import QProcess, QTextStream
import FreeCAD
import CfdTools


class CfdConsoleProcess:
    """ Class to run a console process asynchronously, printing output and
    errors to the FreeCAD console and allowing clean termination in Linux
    and Windows """
    def __init__(self, finishedHook=None, stdoutHook=None, stderrHook=None):
        self.process = QProcess()
        self.finishedHook = finishedHook
        self.stdoutHook = stdoutHook
        self.stderrHook = stderrHook
        self.process.finished.connect(self.finished)
        self.process.readyReadStandardOutput.connect(self.readStdout)
        self.process.readyReadStandardError.connect(self.readStderr)
        self.print_next_error_lines = 0
        self.print_next_error_file = False

    def __del__(self):
        self.terminate()

    def start(self, cmd, env_vars=None, working_dir=None):
        """ Start process and return immediately """
        self.print_next_error_lines = 0
        self.print_next_error_file = False
        env = QtCore.QProcessEnvironment.systemEnvironment()
        if env_vars:
            for key in env_vars:
                env.insert(key, env_vars[key])
        CfdTools.removeAppimageEnvironment(env)
        self.process.setProcessEnvironment(env)
        if working_dir:
            self.process.setWorkingDirectory(working_dir)
        if platform.system() == "Windows":
            # Run through a wrapper process to allow clean termination
            cmd = [os.path.join(FreeCAD.getHomePath(), "bin", "python.exe"),
                   '-u',  # Prevent python from buffering stdout
                   os.path.join(os.path.dirname(__file__), "WindowsRunWrapper.py")] + cmd
        print("Raw command: ", cmd)
        self.process.start(cmd[0], cmd[1:])

    def terminate(self):
        if self.process.state() != self.process.NotRunning:
            if platform.system() == "Windows":
                # terminate() doesn't operate and kill() doesn't allow cleanup and leaves mpi processes running
                # Instead, instruct wrapper program to kill child process and itself cleanly with ctrl-break signal
                self.process.write(b"terminate\n")
                self.process.waitForBytesWritten()  # 'flush'
            else:
                self.process.terminate()
            self.process.waitForFinished()

    def finished(self, exit_code):
        if self.finishedHook:
            self.finishedHook(exit_code)

    def readStdout(self):
        # Ensure only complete lines are passed on
        text = ""
        while self.process.canReadLine():
            byteArr = self.process.readLine()
            text += QTextStream(byteArr).readAll()
        if text:
            print(text, end='')  # Avoid displaying on FreeCAD status bar
            if self.stdoutHook:
                self.stdoutHook(text)
            # Must be at the end as it can cause re-entrance
            if FreeCAD.GuiUp:
                FreeCAD.Gui.updateGui()

    def readStderr(self):
        # Ensure only complete lines are passed on
        # Print any error output to console
        self.process.setReadChannel(QProcess.StandardError)
        text = ""
        while self.process.canReadLine():
            byteArr = self.process.readLine()
            text += QTextStream(byteArr).readAll()
        self.process.setReadChannel(QProcess.StandardOutput)
        if text:
            if self.stderrHook:
                self.stderrHook(text)
            FreeCAD.Console.PrintError(text)
            # Must be at the end as it can cause re-entrance
            if FreeCAD.GuiUp:
                FreeCAD.Gui.updateGui()

    def state(self):
        return self.process.state()

    def waitForStarted(self):
        return self.process.waitForStarted()

    def waitForFinished(self):
        # For some reason waitForFinished doesn't always return - so we resort to a failsafe timeout:
        while True:
            ret = self.process.waitForFinished(1000)
            if self.process.error() != self.process.Timedout:
                self.readStdout()
                self.readStderr()
                return ret
            if self.process.state() == self.process.NotRunning:
                self.readStdout()
                self.readStderr()
                return True

    def exitCode(self):
        return self.process.exitCode()

    def processErrorOutput(self, err):
        """
        Process standard error text output from OpenFOAM
        :param err: Standard error output, single or multiple lines
        :return: A message to be printed on console, or None
        """
        ret = ""
        errlines = err.split('\n')
        for errline in errlines:
            if len(errline) > 0:  # Ignore blanks
                if self.print_next_error_lines > 0:
                    ret += errline + "\n"
                    self.print_next_error_lines -= 1
                if self.print_next_error_file and "file:" in errline:
                    ret += errline + "\n"
                    self.print_next_error_file = False
                words = errline.split(' ', 1)  # Split off first field for parallel
                FATAL = "--> FOAM FATAL ERROR"
                FATALIO = "--> FOAM FATAL IO ERROR"
                if errline.startswith(FATAL) or (len(words) > 1 and words[1].startswith(FATAL)):
                    self.print_next_error_lines = 1
                    ret += "OpenFOAM fatal error:\n"
                elif errline.startswith(FATALIO) or (len(words) > 1 and words[1].startswith(FATALIO)):
                    self.print_next_error_lines = 1
                    self.print_next_error_file = True
                    ret += "OpenFOAM IO error:\n"
                elif errline.startswith("Fatal error:"):
                    ret += errline
        if len(ret) > 0:
            return ret
        else:
            return None
