# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2013-2015 - Juergen Riegel <FreeCAD@juergen-riegel.net> *
# *   Copyright (c) 2015 - Qingfeng Xia <qingfeng.xia()eng.ox.ac.uk>        *
# *   Copyright (c) 2017 Johan Heyns (CSIR) <jheyns@csir.co.za>             *
# *   Copyright (c) 2017 Oliver Oxtoby (CSIR) <ooxtoby@csir.co.za>          *
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

import FreeCAD
import CfdTools
from CfdTools import addObjectProperty
import os
import os.path
if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtCore


def makeCfdSolverFoam(name="CfdSolver"):
    obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython", name)
    _CfdSolverFoam(obj)
    if FreeCAD.GuiUp:
        _ViewProviderCfdSolverFoam(obj.ViewObject)
    return obj


class _CommandCfdSolverFoam:
    def GetResources(self):
        icon_path = os.path.join(CfdTools.get_module_path(), "Gui", "Resources", "icons", "solver.png")
        return {'Pixmap': icon_path,
                'MenuText': QtCore.QT_TRANSLATE_NOOP("Cfd_SolverControl", "Solver job control"),
                'Accel': "S, C",
                'ToolTip': QtCore.QT_TRANSLATE_NOOP("Cfd_SolverControl", "Edit properties and run solver")}

    def IsActive(self):
        return CfdTools.getActiveAnalysis() is not None

    def Activated(self):
        CfdTools.hide_parts_show_meshes()
        isPresent = False
        members = CfdTools.getActiveAnalysis().Group
        for i in members:
            if isinstance(i.Proxy, _CfdSolverFoam):
                FreeCADGui.activeDocument().setEdit(i.Name)
                isPresent = True

        # Allowing user to re-create if CFDSolver was deleted.
        if not isPresent:
            FreeCADGui.addModule("CfdTools")
            FreeCADGui.addModule("CfdSolverFoam")
            FreeCADGui.doCommand("CfdTools.getActiveAnalysis().addObject(CfdSolverFoam.makeCfdSolverFoam())")
            FreeCADGui.doCommand("Gui.activeDocument().setEdit(App.ActiveDocument.ActiveObject.Name)")


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Cfd_SolverControl', _CommandCfdSolverFoam())


class _CfdSolverFoam(object):
    """ Solver-specific properties """
    def __init__(self, obj):
        self.Type = "CfdSolverFoam"
        self.Object = obj  # keep a ref to the DocObj for nonGui usage
        obj.Proxy = self  # link between App::DocumentObject to  this object

        addObjectProperty(obj, "InputCaseName", "case", "App::PropertyFile", "Solver",
                          "Name of case directory where the input files are written")
        addObjectProperty(obj, "Parallel", True, "App::PropertyBool", "Solver",
                          "Parallel analysis on multiple CPU cores")
        addObjectProperty(obj, "ParallelCores", 4, "App::PropertyInteger", "Solver",
                          "Number of cores on which to run parallel analysis")

        addObjectProperty(obj, "MaxIterations", 2000, "App::PropertyInteger", "IterationControl",
                          "Maximum number of iterations to run steady-state analysis")
        addObjectProperty(obj, "SteadyWriteInterval", 100, "App::PropertyFloat", "IterationControl",
                          "Iteration output interval")
        addObjectProperty(obj, "ConvergenceTol", 1e-4, "App::PropertyFloat", "IterationControl",
                          "Global absolute solution convergence criterion")
        addObjectProperty(obj, "EndTime", "1 s", "App::PropertyQuantity", "TimeStepControl",
                          "Total time to run transient solution")
        addObjectProperty(obj, "TimeStep", "0.001 s", "App::PropertyQuantity", "TimeStepControl",
                          "Time step increment")
        addObjectProperty(obj, "TransientWriteInterval", "0.1 s", "App::PropertyQuantity", "IterationControl",
                          "Iteration output interval")

    def execute(self, obj):
        return

    def onChanged(self, obj, prop):
        return

    def __getstate__(self):
        return self.Type

    def __setstate__(self, state):
        if state:
            self.Type = state


class _ViewProviderCfdSolverFoam:
    """A View Provider for the Solver object, base class for all derived solver
    derived solver should implement  a specific TaskPanel and set up solver and override setEdit()"""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        # """after load from FCStd file, self.icon does not exist, return constant path instead"""
        # return ":/icons/fem-solver.svg"
        icon_path = os.path.join(CfdTools.get_module_path(), "Gui", "Resources", "icons", "solver.png")
        return icon_path

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def doubleClicked(self, vobj):
        if FreeCADGui.activeWorkbench().name() != 'CfdOFWorkbench':
            FreeCADGui.activateWorkbench("CfdOFWorkbench")
        doc = FreeCADGui.getDocument(vobj.Object.Document)
        # it should be possible to find the AnalysisObject although it is not a documentObjectGroup
        if not CfdTools.getActiveAnalysis():
            analysis_obj = CfdTools.getParentAnalysisObject(self.Object)
            if analysis_obj:
                CfdTools.setActiveAnalysis(analysis_obj)
            else:
                FreeCAD.Console.PrintError(
                    'No Active Analysis is detected from solver object in the active Document!\n')
        if not doc.getInEdit():
            if CfdTools.getActiveAnalysis().Document is FreeCAD.ActiveDocument:
                if self.Object in CfdTools.getActiveAnalysis().Group:
                    doc.setEdit(vobj.Object.Name)
                else:
                    FreeCAD.Console.PrintError('Activate the analysis this solver belongs to!\n')
            else:
                FreeCAD.Console.PrintError('Active Analysis is not in active Document!\n')
        else:
            FreeCAD.Console.PrintError('Task dialog already open\n')
        return True

    def setEdit(self, vobj, mode):
        if CfdTools.getActiveAnalysis():
            from CfdRunnableFoam import CfdRunnableFoam
            foamRunnable = CfdRunnableFoam(CfdTools.getActiveAnalysis(), self.Object)
            from _TaskPanelCfdSolverControl import _TaskPanelCfdSolverControl
            taskd = _TaskPanelCfdSolverControl(foamRunnable)
            taskd.obj = vobj.Object

            FreeCADGui.Control.showDialog(taskd)
        return True

    def unsetEdit(self, vobj, mode):
        FreeCADGui.Control.closeDialog()
        return

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

