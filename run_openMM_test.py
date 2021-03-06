#!/usr/bin/env python

from simtk.openmm.app import *
from simtk.openmm import *
from simtk.unit import *
from sys import stdout
from time import gmtime, strftime
from datetime import datetime
from copy import deepcopy
import os
import sys
import numpy
import argparse
import shutil

from subroutines import *

parser = argparse.ArgumentParser()
parser.add_argument("pdb", type=str, help="PDB file with initial positions")
parser.add_argument("--nstep", type=str, help="number of step for updating forces")
parser.add_argument("--volt", type=str, help="applied potential (V)")
parser.add_argument("--nsec", type=str, help="simulation time (ns)")
args = parser.parse_args()

if args.nstep is not None:
    outPath = 'output_' + args.nstep + "step_" + args.volt + "V_" + args.nsec + "ns"
else:
    outPath = "output" + strftime("%s",gmtime())

if os.path.exists(outPath):
    shutil.rmtree(outPath)

strdir ='../'
os.mkdir(outPath)
os.chdir(outPath)
chargesFile = open("charges.dat", "w")
print(outPath)

pdb = args.pdb
sim = MDsimulation( 
        pdb, 
        ResidueConnectivityFiles = [
                strdir + 'ffdir/sapt_residues.xml', 
                strdir + 'ffdir/graph_residue_c.xml'
        ],
        FF_files = [
                strdir + 'ffdir/sapt_noDB.xml', 
                strdir + 'ffdir/graph_c_freeze.xml'
        ], 
        FF_Efield_files = [
                strdir + 'ffdir/sapt_Efield_noDB.xml', 
                strdir + 'ffdir/graph_c_freeze.xml'
        ]
)

sim.equilibration()

print('Starting Production NPT Simulation...')

# interior sheets which have fluctuation charges are now labeled "grpc", and sheets which remain neutral are labeled
# "grph"
grpc = Graph_list("grpc")
grpc.grpclist(sim)
grp_dummy = Graph_list("grpd")
grp_dummy.grpclist(sim)

graph = deepcopy(grpc.cathode)
graph.extend(deepcopy(grp_dummy.dummy))
graph.extend(deepcopy(grpc.anode))
assert len(graph) == 2400

# H atoms of solution
HofBMI = solution_Hlist("BMIM")
HofBMI.cation_hlist(sim)
BofBF4 = solution_Hlist("BF4")
BofBF4.anion_hlist(sim)
HofACN = solution_Hlist("acnt")
HofACN.solvent_hlist(sim)
He = solution_Hlist("Hel")
He.vac_list(sim)

merge_Hlist= deepcopy(HofBMI.cation)
merge_Hlist.extend( deepcopy(BofBF4.anion) )
merge_Hlist.extend( deepcopy(HofACN.solvent) )
He_list = deepcopy(He.He)

# all atoms of solution
BMIM = solution_allatom("BMIM")
BMIM.res_list(sim)
BF4 = solution_allatom("BF4")
BF4.res_list(sim)
acnt = solution_allatom("acnt")
acnt.res_list(sim)

solvent_list = deepcopy(BMIM.atomlist)
solvent_list.extend(deepcopy(BF4.atomlist))
solvent_list.extend(deepcopy(acnt.atomlist))

# add exclusions for intra-sheet non-bonded interactions.
sim.exlusionNonbondedForce(graph)
state = sim.simmd.context.getState(getEnergy=True,getForces=True,getVelocities=True,getPositions=True,getParameters=True)
initialPositions = state.getPositions()
cell_dist = Distance(grpc.c562_1, grpc.c562_2, initialPositions)
print('cathode-anode distance (nm)', cell_dist)

boxVecs = sim.simmd.topology.getPeriodicBoxVectors()
crossBox = numpy.cross(boxVecs[0], boxVecs[1])
sheet_area = numpy.dot(crossBox, crossBox)**0.5 / nanometer**2
print(sheet_area)

sim.simmd.context.reinitialize()
sim.simEfield.context.reinitialize()
sim.simmd.context.setPositions(initialPositions)
sim.simEfield.context.setPositions(initialPositions)


#************ get rid of the MD loop, just calculating converged charges ***********
Ngraphene_atoms = len(graph)

# one sheet here
area_atom = sheet_area / (Ngraphene_atoms / 2) # this is the surface area per graphene atom in nm^2
conv = 18.8973 / 2625.5  # bohr/nm * au/(kJ/mol)
# z box coordinate (nm)
zbox=boxVecs[2][2] / nanometer
Lgap = (zbox - cell_dist) # length of vacuum gap in nanometers, set by choice of simulation box (z-dimension)
print('length of vacuum gap (nm)', Lgap)
Niter_max = 3  # maximum steps for convergence
tol=0.01 # tolerance for average deviation of charges between iteration
Voltage = float(args.volt)  # external voltage in Volts
Voltage = Voltage * 96.487  # convert eV to kJ/mol to work in kJ/mol
q_max = 2.0  # Don't allow charges bigger than this, no physical reason why they should be this big
f_iter = int(( float(args.nsec) * 1000000 / int(args.nstep) )) + 1  # number of iterations for charge equilibration
#print('number of iterations', f_iter)
small = 1e-4

sim.initializeCharge( Ngraphene_atoms, graph, area_atom, Voltage, Lgap, conv, small)

allEz_cell = []
allEx_i = []
allEy_i = []
allEz_i = []
for i in range(1, f_iter ):
    print()
    print(i,datetime.now())

    sim.simmd.step( int(args.nstep) )

    state = sim.simmd.context.getState(getEnergy=True,getForces=True,getPositions=True)
    print(str(state.getKineticEnergy()))
    print(str(state.getPotentialEnergy()))

    positions = state.getPositions()
    sim.simEfield.context.setPositions(positions)
    sim.ConvergedCharge( Niter_max, Ngraphene_atoms, graph, area_atom, Voltage, Lgap, conv, q_max )
    sim.FinalCharge(Ngraphene_atoms, graph, args, i, chargesFile)
    print('Charges converged, Energies from full Force Field')
    sim.PrintFinalEnergies()

    state2 = sim.simEfield.context.getState(getEnergy=True,getForces=True,getPositions=True)
    forces = state2.getForces()
    ind_Q = get_Efield(solvent_list)
    Q_Cat, Q_An = ind_Q.induced_q(1.15, 5.8402, cell_dist, sim, positions)
    print('Analytically compute the induced charge on sheets (Q_Cat, Q_An):', Q_Cat, Q_An)

# get electric field and position for BMIM, acetonitrile, BF4 along z-dimension
    Efield_cell_i = get_Efield(merge_Hlist)
    Efield_cell_i.efield(sim, forces)
    Efield_cell_i.Pos_z(positions)
    hist1 = hist_Efield(0.025,14, Efield_cell_i.position_z, Efield_cell_i.efieldz)
    allEz_cell.append( hist1.Efield() )
  
# get electric field and position for He atoms
    Efield_vac_i = get_Efield(He_list)
    Efield_vac_i.efield(sim, forces)
    Efield_vac_i.Pos_z(positions)
    hist_Ex = hist_Efield(0.025,14, Efield_vac_i.position_z, Efield_vac_i.efieldx)
    hist_Ey = hist_Efield(0.025,14, Efield_vac_i.position_z, Efield_vac_i.efieldy)
    hist_Ez = hist_Efield(0.025,14, Efield_vac_i.position_z, Efield_vac_i.efieldz)
    allEx_i.append( hist_Ex.Efield() )
    allEy_i.append( hist_Ey.Efield() )
    allEz_i.append( hist_Ez.Efield() )
    
meanEz_cell = [sum(e)/len(e) for e in zip(*allEz_cell)]
meanEx = [sum(e)/len(e) for e in zip(*allEx_i)]
meanEy = [sum(e)/len(e) for e in zip(*allEy_i)]
meanEz = [sum(e)/len(e) for e in zip(*allEz_i)]
hist1.save_hist(meanEz_cell, "Ez_cell_hist.dat")
hist_Ex.save_hist(meanEx, "Ex_hist.dat")
hist_Ey.save_hist(meanEy, "Ey_hist.dat")
hist_Ez.save_hist(meanEz, "Ez_hist.dat")

print('Done!')

exit()
