# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2013-2015 - Juergen Riegel <FreeCAD@juergen-riegel.net> *
# *   Copyright (c) 2017 Alfred Bogaers (CSIR) <abogaers@csir.co.za>        *
# *   Copyright (c) 2017 Johan Heyns (CSIR) <jheyns@csir.co.za>             *
# *   Copyright (c) 2017 Oliver Oxtoby (CSIR) <ooxtoby@csir.co.za>          *
# *   Copyright (c) 2019-2020 Oliver Oxtoby <oliveroxtoby@gmail.com>        *
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
from CfdTools import cfdMessage
import os
import os.path
import shutil
from FreeCAD import Units
import TemplateBuilder
import CfdMeshRefinement


class CfdCaseWriterFoam:
    def __init__(self, analysis_obj):
        self.analysis_obj = analysis_obj
        self.solver_obj = CfdTools.getSolver(analysis_obj)
        self.physics_model = CfdTools.getPhysicsModel(analysis_obj)
        self.mesh_obj = CfdTools.getMesh(analysis_obj)
        self.material_objs = CfdTools.getMaterials(analysis_obj)
        self.bc_group = CfdTools.getCfdBoundaryGroup(analysis_obj)
        self.initial_conditions = CfdTools.getInitialConditions(analysis_obj)
        self.porousZone_objs = CfdTools.getPorousZoneObjects(analysis_obj)
        self.initialisationZone_objs = CfdTools.getInitialisationZoneObjects(analysis_obj)
        self.zone_objs = CfdTools.getZoneObjects(analysis_obj)
        self.mesh_generated = False
        self.working_dir = CfdTools.getOutputPath(self.analysis_obj)

    def writeCase(self, progressCallback=None):
        """ writeCase() will collect case settings, and finally build a runnable case. """
        cfdMessage("Writing case to folder {}\n".format(self.working_dir))
        if not os.path.exists(self.working_dir):
            raise IOError("Path " + self.working_dir + " does not exist.")

        # Perform initialisation here rather than __init__ in case of path changes
        self.case_folder = os.path.join(self.working_dir, self.solver_obj.InputCaseName)
        self.case_folder = os.path.expanduser(os.path.abspath(self.case_folder))
        self.mesh_file_name = os.path.join(self.case_folder, self.solver_obj.InputCaseName, u".unv")

        self.template_path = os.path.join(CfdTools.get_module_path(), "data", "defaults")

        # Collect settings into single dictionary
        if not self.mesh_obj:
            raise RuntimeError("No mesh object found in analysis")
        phys_settings = CfdTools.propsToDict(self.physics_model)

        # Validate BC labels
        bc_labels = [b.Label for b in self.bc_group]
        for i, l in enumerate(bc_labels):
            if bc_labels[i].find(' ') >= 0:
                raise ValueError("Boundary condition label '" + bc_labels[i] + "' is not valid: May not contain spaces")
        for i, l in enumerate(bc_labels):
            for j in range(i+1, len(bc_labels)):
                if bc_labels[j] == l:
                    raise ValueError("Boundary condition label '" + bc_labels[i] + "' is duplicated")

        self.settings = {
            'physics': phys_settings,
            'fluidProperties': [],  # Order is important, so use a list
            'initialValues': CfdTools.propsToDict(self.initial_conditions),
            'boundaries': dict((b.Label, CfdTools.propsToDict(b)) for b in self.bc_group),
            'bafflesPresent': self.bafflesPresent(),
            'porousZones': {},
            'porousZonesPresent': False,
            'initialisationZones': {o.Label: CfdTools.propsToDict(o) for o in self.initialisationZone_objs},
            'initialisationZonesPresent': len(self.initialisationZone_objs) > 0,
            'zones': {o.Label: {'PartNameList': tuple(r[0] for r in o.References)} for o in self.zone_objs},
            'zonesPresent': len(self.zone_objs) > 0,
            'meshType': self.mesh_obj.Proxy.Type,
            'meshDimension': self.mesh_obj.ElementDimension,
            'meshDir': "../"+self.mesh_obj.CaseName,
            'solver': CfdTools.propsToDict(self.solver_obj),
            'system': {},
            'runChangeDictionary': False
            }

        self.processSystemSettings()
        self.processSolverSettings()
        self.processFluidProperties()
        self.processBoundaryConditions()
        self.processInitialConditions()
        self.clearCase()

        self.exportZoneStlSurfaces()
        if self.porousZone_objs:
            self.processPorousZoneProperties()
        self.processInitialisationZoneProperties()

        self.settings['createPatchesFromSnappyBaffles'] = False
        cfdMessage("Matching boundary conditions ...\n")
        if progressCallback:
            progressCallback("Matching boundary conditions ...")
        self.setupPatchNames()

        TemplateBuilder.TemplateBuilder(self.case_folder, self.template_path, self.settings)

        # Update Allrun permission - will fail silently on Windows
        fname = os.path.join(self.case_folder, "Allrun")
        import stat
        s = os.stat(fname)
        os.chmod(fname, s.st_mode | stat.S_IEXEC)

        cfdMessage("Successfully wrote case to folder {}\n".format(self.working_dir))
        return True

    def getSolverName(self):
        """ Solver name is selected based on selected physics. This should only be extended as additional physics are
        included. """
        solver = None
        if self.physics_model.Phase == 'Single':
            if len(self.material_objs) == 1:
                if self.physics_model.Flow == 'Incompressible':
                    if self.physics_model.Thermal == 'None':
                        if self.physics_model.Time == 'Transient':
                            solver = 'pimpleFoam'
                        else:
                            if self.porousZone_objs or self.porousBafflesPresent():
                                solver = 'porousSimpleFoam'
                            else:
                                solver = 'simpleFoam'
                    else:
                        raise RuntimeError("Only isothermal simulation currently supported for incompressible flow.")
                elif self.physics_model.Flow == 'Compressible':
                    if self.physics_model.Time == 'Transient':
                        solver = 'buoyantPimpleFoam'
                    else:
                        solver = 'buoyantSimpleFoam'
                elif self.physics_model.Flow == 'HighMachCompressible':
                    solver = 'hisa'
                else:
                    raise RuntimeError(self.physics_model.Flow + " flow model currently not supported.")
            else:
                raise RuntimeError("Only one material object may be present for single phase simulation.")
        elif self.physics_model.Phase == 'FreeSurface':
            if self.physics_model.Time == 'Transient':
                if self.physics_model.Thermal == 'None':
                    if len(self.material_objs) == 2:
                        solver = 'interFoam'
                    elif len(self.material_objs) > 2:
                        solver = 'multiphaseInterFoam'
                    else:
                        raise RuntimeError("At least two material objects must be present for free surface simulation.")
                else:
                    raise RuntimeError("Only isothermal analysis is currently supported for free surface flow simulation.")
            else:
                raise RuntimeError("Only transient analysis is supported for free surface flow simulation.")
        else:
            raise RuntimeError(self.physics_model.Phase + " phase model currently not supported.")

        # Catch-all in case
        if solver is None:
            raise RuntimeError("No solver is supported to handle the selected physics with {} phases.".format(
                len(self.material_objs)))
        return solver

    def processSolverSettings(self):
        solver_settings = self.settings['solver']
        if solver_settings['Parallel']:
            if solver_settings['ParallelCores'] < 2:
                solver_settings['ParallelCores'] = 2
        solver_settings['SolverName'] = self.getSolverName()

    def processSystemSettings(self):
        system_settings = self.settings['system']
        system_settings['FoamRuntime'] = CfdTools.getFoamRuntime()
        system_settings['CasePath'] = self.case_folder
        system_settings['FoamPath'] = CfdTools.getFoamDir()
        if CfdTools.getFoamRuntime() != 'WindowsDocker':
            system_settings['TranslatedFoamPath'] = CfdTools.translatePath(CfdTools.getFoamDir())

    def clearCase(self, backup_path=None):
        """ Remove and recreate case directory, optionally backing up """
        output_path = self.case_folder
        if backup_path and os.path.isdir(output_path):
            shutil.move(output_path, backup_path)
        if os.path.isdir(output_path):
            shutil.rmtree(output_path)
        os.makedirs(output_path)  # mkdir -p

    def setupMesh(self, updated_mesh_path, scale):
        if os.path.exists(updated_mesh_path):
            CfdTools.convertMesh(self.case_folder, updated_mesh_path, scale)

    def processFluidProperties(self):
        # self.material_obj currently stores everything as a string
        # Convert to (mostly) SI numbers for OpenFOAM
        settings = self.settings
        for material_obj in self.material_objs:
            mp = material_obj.Material
            mp['Name'] = material_obj.Label
            if 'Density' in mp:
                mp['Density'] = Units.Quantity(mp['Density']).getValueAs("kg/m^3").Value
            if 'DynamicViscosity' in mp:
                if self.physics_model.Turbulence == 'Inviscid':
                    mp['DynamicViscosity'] = 0.0
                else:
                    mp['DynamicViscosity'] = Units.Quantity(mp['DynamicViscosity']).getValueAs("kg/m/s").Value
                mp['KinematicViscosity'] = mp['DynamicViscosity']/mp['Density']
            if 'MolarMass' in mp:
                # OpenFOAM uses kg/kmol
                mp['MolarMass'] = Units.Quantity(mp['MolarMass']).getValueAs("kg/mol").Value*1000
            if 'Cp' in mp:
                mp['Cp'] = Units.Quantity(mp['Cp']).getValueAs("J/kg/K").Value
            if 'SutherlandTemperature' in mp:
                mp['SutherlandTemperature'] = Units.Quantity(mp['SutherlandTemperature']).getValueAs("K").Value
                if 'SutherlandRefViscosity' in mp and 'SutherlandRefTemperature' in mp:
                    mu0 = Units.Quantity(mp['SutherlandRefViscosity']).getValueAs("kg/m/s").Value
                    T0 = Units.Quantity(mp['SutherlandRefTemperature']).getValueAs("K").Value
                    mp['SutherlandConstant'] = mu0/T0**(3./2)*(T0+mp['SutherlandTemperature'])
            settings['fluidProperties'].append(mp)

    def processBoundaryConditions(self):
        """ Compute any quantities required before case build """
        settings = self.settings
        for bc_name in settings['boundaries']:
            bc = settings['boundaries'][bc_name]
            if not bc['VelocityIsCartesian']:
                veloMag = bc['VelocityMag']
                face = bc['DirectionFace'].split(':')
                if not face[0]:
                    face = bc['References'][0]
                # See if entered face actually exists and is planar
                try:
                    selected_object = self.analysis_obj.Document.getObject(face[0])
                    if hasattr(selected_object, "Shape"):
                        elt = selected_object.Shape.getElement(face[1])
                        if elt.ShapeType == 'Face' and CfdTools.is_planar(elt):
                            n = elt.normalAt(0.5, 0.5)
                            if bc['ReverseNormal']:
                               n = [-ni for ni in n]
                            velocity = [ni*veloMag for ni in n]
                            bc['Ux'] = velocity[0]
                            bc['Uy'] = velocity[1]
                            bc['Uz'] = velocity[2]
                        else:
                            raise RuntimeError
                    else:
                        raise RuntimeError
                except (SystemError, RuntimeError):
                    raise RuntimeError(str(bc['DirectionFace']) + " is not a valid, planar face.")
            if settings['solver']['SolverName'] in ['simpleFoam', 'porousSimpleFoam', 'pimpleFoam']:
                bc['KinematicPressure'] = bc['Pressure']/settings['fluidProperties'][0]['Density']

            if bc['PorousBaffleMethod'] == 'porousScreen':
                wireDiam = bc['ScreenWireDiameter']
                spacing = bc['ScreenSpacing']
                CD = 1.0  # Drag coeff of wire (Simmons - valid for Re > ~300)
                beta = (1-wireDiam/spacing)**2
                bc['PressureDropCoeff'] = CD*(1-beta)

            if settings['solver']['SolverName'] in ['interFoam', 'multiphaseInterFoam']:
                # Make sure the first n-1 alpha values exist, and write the n-th one
                # consistently for multiphaseInterFoam
                sum_alpha = 0.0
                alphas_new = {}
                for i, m in enumerate(settings['fluidProperties']):
                    alpha_name = m['Name']
                    if i == len(settings['fluidProperties']) - 1:
                        if settings['solver']['SolverName'] == 'multiphaseInterFoam':
                            alphas_new[alpha_name] = 1.0 - sum_alpha
                    else:
                        alpha = Units.Quantity(bc.get('VolumeFractions', {}).get(alpha_name, '0')).Value
                        alphas_new[alpha_name] = alpha
                        sum_alpha += alpha
                bc['VolumeFractions'] = alphas_new

    def processInitialConditions(self):
        """ Do any required computations before case build. Boundary conditions must be processed first. """
        settings = self.settings
        initial_values = settings['initialValues']
        if settings['solver']['SolverName'] in ['simpleFoam', 'porousSimpleFoam', 'pimpleFoam']:
            mat_prop = settings['fluidProperties'][0]
            initial_values['KinematicPressure'] = initial_values['Pressure'] / mat_prop['Density']
        if settings['solver']['SolverName'] in ['interFoam', 'multiphaseInterFoam']:
            # Make sure the first n-1 alpha values exist, and write the n-th one
            # consistently for multiphaseInterFoam
            sum_alpha = 0.0
            alphas_new = {}
            for i, m in enumerate(settings['fluidProperties']):
                alpha_name = m['Name']
                if i == len(settings['fluidProperties'])-1:
                    if settings['solver']['SolverName'] == 'multiphaseInterFoam':
                        alphas_new[alpha_name] = 1.0-sum_alpha
                else:
                    alpha = Units.Quantity(initial_values.get('VolumeFractions', {}).get(alpha_name, '0')).Value
                    alphas_new[alpha_name] = alpha
                    sum_alpha += alpha
            initial_values['VolumeFractions'] = alphas_new

        if initial_values['PotentialFlowP']:
            if settings['solver']['SolverName'] not in ['simpleFoam', 'porousSimpleFoam', 'pimpleFoam', 'hisa']:
                raise RuntimeError("Selected solver does not support potential pressure initialisation.")

        physics = settings['physics']

        # Copy velocity
        if initial_values['UseInletUValues']:
            if initial_values['BoundaryU']:
                inlet_bc = settings['boundaries'][initial_values['BoundaryU'].Label]
                if inlet_bc['BoundarySubType'] == 'uniformVelocityInlet' or inlet_bc['BoundarySubType'] == 'farField':
                    initial_values['Ux'] = inlet_bc['Ux']
                    initial_values['Uy'] = inlet_bc['Uy']
                    initial_values['Uz'] = inlet_bc['Uz']
                else:
                    raise RuntimeError("Boundary type not appropriate to determine initial velocity.")
            else:
                raise RuntimeError("No boundary selected to copy initial velocity value from.")

        # Copy pressure
        if initial_values['UseOutletPValue']:
            if initial_values['BoundaryP']:
                outlet_bc = settings['boundaries'][initial_values['BoundaryP'].Label]
                if outlet_bc['BoundarySubType'] == 'staticPressureOutlet' or \
                        outlet_bc['BoundarySubType'] == 'totalPressureOpening' or \
                        outlet_bc['BoundarySubType'] == 'totalPressureInlet' or \
                        outlet_bc['BoundarySubType'] == 'staticPressureInlet' or \
                        outlet_bc['BoundarySubType'] == 'farField':
                    initial_values['Pressure'] = outlet_bc['Pressure']
                else:
                    raise RuntimeError("Boundary type not appropriate to determine initial pressure.")
            else:
                raise RuntimeError("No boundary selected to copy initial pressure value from.")

        if physics['Thermal'] == 'Energy' and initial_values['UseInletTemperatureValue']:
            inlet_bc = settings['boundaries'][initial_values['BoundaryT'].Label]
            if inlet_bc['BoundaryType'] == 'inlet':
                if inlet_bc['ThermalBoundaryType'] == 'fixedValue':
                    initial_values['Temperature'] = inlet_bc['Temperature']
                else:
                    raise RuntimeError("Inlet type not appropriate to determine initial temperature")
            elif inlet_bc['BoundarySubType'] == 'farField':
                initial_values['Temperature'] = inlet_bc['Temperature']
            else:
                raise RuntimeError("Inlet type not appropriate to determine initial temperature.")

        # Copy turbulence settings
        if physics['TurbulenceModel'] is not None:
            if initial_values['UseInletTurbulenceValues']:
                if initial_values['BoundaryTurb']:
                    inlet_bc = settings['boundaries'][initial_values['BoundaryTurb'].Label]
                    if inlet_bc['TurbulenceInletSpecification'] == 'TKEAndSpecDissipationRate':
                        initial_values['k'] = inlet_bc['TurbulentKineticEnergy']
                        initial_values['omega'] = inlet_bc['SpecificDissipationRate']
                    elif inlet_bc['TurbulenceInletSpecification'] == 'intensityAndLengthScale':
                        if inlet_bc['BoundarySubType'] == 'uniformVelocity' or \
                           inlet_bc['BoundarySubType'] == 'farField':
                            Uin = (inlet_bc['Ux']**2 +
                                   inlet_bc['Uy']**2 +
                                   inlet_bc['Uz']**2)**0.5
                            I = inlet_bc['TurbulenceIntensity']
                            k = 3/2*(Uin*I)**2
                            Cmu = 0.09  # Standard turb model parameter
                            l = inlet_bc['TurbulenceLengthScale']
                            omega = k**0.5/(Cmu**0.25*l)
                            initial_values['k'] = k
                            initial_values['omega'] = omega
                        else:
                            raise RuntimeError(
                                "Inlet type currently unsupported for copying turbulence initial conditions.")
                    else:
                        raise RuntimeError(
                            "Turbulence inlet specification currently unsupported for copying turbulence initial conditions")
                else:
                    raise RuntimeError("No boundary selected to copy initial turbulence values from.")

    # Zones

    def exportZoneStlSurfaces(self):
        for zo in self.zone_objs:
            for r in zo.References:
                path = os.path.join(self.working_dir,
                                    self.solver_obj.InputCaseName,
                                    "constant",
                                    "triSurface")
                if not os.path.exists(path):
                    os.makedirs(path)
                fname = os.path.join(path, r[0]+u".stl")
                import MeshPart
                sel_obj = self.analysis_obj.Document.getObject(r[0])
                shape = sel_obj.Shape
                meshStl = MeshPart.meshFromShape(shape, LinearDeflection=self.mesh_obj.STLLinearDeflection)
                meshStl.write(fname)
                print("Successfully wrote stl surface\n")

    def processPorousZoneProperties(self):
        settings = self.settings
        settings['porousZonesPresent'] = True
        porousZoneSettings = settings['porousZones']
        for po in self.porousZone_objs:
            pd = {'PartNameList': tuple(r[0] for r in po.References)}
            po = CfdTools.propsToDict(po)
            if po['PorousCorrelation'] == 'DarcyForchheimer':
                pd['D'] = (po['D1'], po['D2'], po['D3'])
                pd['F'] = (po['F1'], po['F2'], po['F3'])
                pd['e1'] = tuple(po['e1'])
                pd['e3'] = tuple(po['e3'])
            elif po['PorousCorrelation'] == 'Jakob':
                # Calculate effective Darcy-Forchheimer coefficients
                # This is for equilateral triangles arranged with the triangles pointing in BundleLayerNormal
                # direction (direction of greater spacing - sqrt(3)*triangleEdgeLength)
                pd['e1'] = tuple(po['SpacingDirection'])  # OpenFOAM modifies to be orthog to e3
                pd['e3'] = tuple(po['TubeAxis'])
                spacing = po['TubeSpacing']
                d0 = po['OuterDiameter']
                u0 = po['VelocityEstimate']
                aspectRatio = po['AspectRatio']
                kinVisc = self.settings['fluidProperties']['KinematicViscosity']
                if kinVisc == 0.0:
                    raise ValueError("Viscosity must be set for Jakob correlation")
                if spacing < d0:
                    raise ValueError("Tube spacing may not be less than diameter")
                D = [0, 0, 0]
                F = [0, 0, 0]
                for (i, Sl, St) in [(0, aspectRatio*spacing, spacing), (1, spacing, aspectRatio*spacing)]:
                    C = 1.0/St*0.5*(1.0+0.47/(Sl/d0-1)**1.06)*(1.0/(1-d0/Sl))**(2.0-0.16)
                    Di = C/d0*0.5*(u0*d0/kinVisc)**(1.0-0.16)
                    Fi = C*(u0*d0/kinVisc)**(-0.16)
                    D[i] = Di
                    F[i] = Fi
                pd['D'] = tuple(D)
                pd['F'] = tuple(F)
                # Currently assuming zero drag parallel to tube bundle (3rd principal dirn)
            else:
                raise RuntimeError("Unrecognised method for porous baffle resistance")
            porousZoneSettings[po['Label']] = pd

    def processInitialisationZoneProperties(self):
        settings = self.settings
        if settings['solver']['SolverName'] in ['interFoam', 'multiphaseInterFoam']:
            # Make sure the first n-1 alpha values exist, and write the n-th one
            # consistently for multiphaseInterFoam
            for zone_name in settings['initialisationZones']:
                z = settings['initialisationZones'][zone_name]
                sum_alpha = 0.0
                if 'VolumeFractions' in z:
                    alphas_new = {}
                    for i, m in enumerate(settings['fluidProperties']):
                        alpha_name = m['Name']
                        if i == len(settings['fluidProperties'])-1:
                            if settings['solver']['SolverName'] == 'multiphaseInterFoam':
                                alphas_new[alpha_name] = 1.0-sum_alpha
                        else:
                            alpha = Units.Quantity(z['VolumeFractions'].get(alpha_name, '0')).Value
                            alphas_new[alpha_name] = alpha
                            sum_alpha += alpha
                    z['VolumeFractions'] = alphas_new

    def bafflesPresent(self):
        for b in self.bc_group:
            if b.BoundaryType == 'baffle':
                return True
        return False

    def porousBafflesPresent(self):
        for b in self.bc_group:
            if b.BoundaryType == 'baffle' and \
               b.BoundarySubType == 'porousBaffle':
                return True
        return False

    def setupPatchNames(self):
        print('Populating createPatchDict to update BC names')
        settings = self.settings
        settings['createPatches'] = {}
        settings['createPatchesSnappyBaffles'] = {}
        bc_group = self.bc_group

        for bc_id, bc_obj in enumerate(bc_group):
            bcType = bc_obj.BoundaryType
            bcSubType = bc_obj.BoundarySubType
            patchType = CfdTools.getPatchType(bcType, bcSubType)
            settings['createPatches'][bc_obj.Label] = {
                'PatchNamesList': '"patch_'+str(bc_id+1)+'_.*"',
                'PatchType': patchType
            }

            if bcType == 'baffle' and self.mesh_obj.MeshUtility == 'snappyHexMesh':
                settings['createPatchesFromSnappyBaffles'] = True
                settings['createPatchesSnappyBaffles'][bc_obj.Label] = {
                    'PatchNamesList': '"'+bc_obj.Name+'_.*"',
                    'PatchNamesListSlave': '"'+bc_obj.Name+'_.*_slave"'}

        # Add default faces
        settings['createPatches']['defaultFaces'] = {
            'PatchNamesList': '"patch_0_.*"',
            'PatchType': "patch"
        }
