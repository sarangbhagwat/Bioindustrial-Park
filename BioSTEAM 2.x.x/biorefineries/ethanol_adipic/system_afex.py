#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# BioSTEAM: The Biorefinery Simulation and Techno-Economic Analysis Modules
# Copyright (C) 2020, Yoel Cortes-Pena <yoelcortes@gmail.com>
# Bioindustrial-Park: BioSTEAM's Premier Biorefinery Models and Results
# Copyright (C) 2020, Yalin Li <yalinli2@illinois.edu> (this biorefinery)
# 
# This module is under the UIUC open-source license. See 
# github.com/BioSTEAMDevelopmentGroup/biosteam/blob/master/LICENSE.txt
# for license details.

"""
References
----------
[1] Humbird et al., Process Design and Economics for Biochemical Conversion of 
    Lignocellulosic Biomass to Ethanol: Dilute-Acid Pretreatment and Enzymatic 
    Hydrolysis of Corn Stover; Technical Report NREL/TP-5100-47764; 
    National Renewable Energy Lab (NREL), 2011.
    https://www.nrel.gov/docs/fy11osti/47764.pdf
[2] Davis et al., Process Design and Economics for the Conversion of Lignocellulosic 
    Biomass to Hydrocarbon Fuels and Coproducts: 2018 Biochemical Design Case Update; 
    NREL/TP-5100-71949; National Renewable Energy Lab (NREL), 2018. 
    https://doi.org/10.2172/1483234
[3] Lamers et al. Techno-Economic Analysis of Decentralized Biomass
    Processing Depots. Bioresource Technology 2015, 194, 205–213.
    https://doi.org/10.1016/j.biortech.2015.07.009.

Naming conventions:
    D = Distillation column
    F = Flash tank
    H = Heat exchange
    M = Mixer
    P = Pump
    R = Reactor
    S = Splitter (including solid/liquid separator)
    T = Tank or bin for storage
    U = Other units

Processes:
    100: Feedstock preprocessing
    200: Pretreatment
    300: Carbohydrate conversion
    400: Ethanol purification
    500: Lignin utilization (not included in this biorefinery)
    600: Wastewater treatment
    700: Facilities

"""

# %%

import biosteam as bst
import thermosteam as tmo
from biosteam import System
from thermosteam import Stream
from biorefineries.ethanol_adipic import _units as units
from biorefineries.ethanol_adipic import _facilities as facilities
from biorefineries.ethanol_adipic._chemicals import chems, chemical_groups, \
    soluble_organics, solubles, insolubles, COD_chemicals, combustibles
from biorefineries.ethanol_adipic._process_settings import \
    price, GWP_CF_stream, GWP_CFs, _labor_2011to2016, set_feedstock_price
from biorefineries.ethanol_adipic._utils import convert_ethanol_wt_2_mol, \
    find_split, splits_df
from biorefineries.ethanol_adipic._tea import ethanol_adipic_TEA

bst.CE = 541.7 # year 2016
flowsheet = bst.Flowsheet('AFEX')
bst.main_flowsheet.set_flowsheet(flowsheet)
bst.settings.set_thermo(chems)
auom = tmo.units_of_measure.AbsoluteUnitsOfMeasure


# %%

# =============================================================================
# Feedstock preprocessing at the depot
# =============================================================================

from biorefineries.ethanol_adipic._preprocessing import \
    create_default_depot, PreprocessingCost

prep_sys = create_default_depot(kind='CPP', with_AFEX=True,
                                water_price=price['Makeup water'],
                                ammonia_price=price['NH3'],
                                natural_gas_price=price['Natural gas'])

prep_sys.simulate()

prep_cost = PreprocessingCost(depot_sys=prep_sys,
                              labor_adjustment=_labor_2011to2016)

(U101, U102, U103, U104, U105, U106) = sorted(prep_sys.units, key=lambda u: u.ID)
fugative_ammonia, feedstock = (i.copy() for i in sorted(prep_sys.products,
                                                        key=lambda s: s.ID))

# $/Mg
set_feedstock_price(feedstock, preprocessing=prep_cost.feedstock_unit_price)
# If want to use the default preprocessing price ($24.35/Mg)
# set_feedstock_price(feedstock)

# If want to use the price in ref [2], note that the price here is $/dry U.S. ton
# feedstock.price = price['Feedstock']


# %%

# =============================================================================
# Fermentation streams
# =============================================================================

# Flow updated in EnzymeHydrolysateMixer
enzyme_M301 = Stream('enzyme_M301', units='kg/hr', price=price['Enzyme'])
# Used to adjust enzymatic hydrolysis solid loading, flow updated in EnzymeHydrolysateMixer
water_M301 = Stream('water_M301', units='kg/hr')

# Streams 311 and 309 from ref [1]
CSL_R301 = Stream('CSL_R301', units='kg/hr')
CSL_R302 = Stream('CSL_R302', units='kg/hr')

# Streams 312 and 310 from ref [1]
DAP_R301 = Stream('DAP_R301', units='kg/hr')
DAP_R302 = Stream('DAP_R302', units='kg/hr')


# =============================================================================
# Fermentation units
# =============================================================================

#!!! Higher enzyme loading to account for Xylan???
M301 = units.EnzymeHydrolysateMixer('M301', ins=(feedstock, enzyme_M301, water_M301),
                                    enzyme_loading=20, solid_loading=0.2)

H301 = bst.units.HXutility('H301', ins=M301-0, T=55+273.15)

R301 = units.SaccharificationAndCoFermentation('R301', ins=(H301-0, '', 
                                                            CSL_R301, DAP_R301),
                                                outs=('R301_g', 'effluent', 'side_draw'),
                                                C5_saccharification=True)
# R301.saccharification_rxns_C5.X[0] = 0.8
# R301.saccharification_rxns_C5.X[1] = 0

# Followed ref [2], no sorbitol in the final seed fermenter as in ref [1]
R302 = units.SeedTrain('R302', ins=(R301-2, CSL_R302, DAP_R302),
                          outs=('R302_g', 'seed'))
T301 = units.SeedHoldTank('T301', ins=R302-1, outs=1-R301)

fermentation_sys = System('fermentation_sys', 
                          path=(M301, H301, R301, R302, T301), recycle=R302-1)



# %%

# =============================================================================
# Ethanol purification
# =============================================================================

water_U401 = Stream('water_U401', units='kg/hr')

M401 = bst.units.Mixer('M401', ins=(R301-0, R302-0), outs='fermentation_vapor')
def update_U401_water():
    M401._run()
    # 26836 and 21759 from streams 524 and 523 in ref [1]
    water_U401.imass['Water'] = 26836/21759 * M401.F_mass_in
M401.specification = update_U401_water

U401 = bst.units.VentScrubber('U401', ins=(water_U401, M401-0),
                              outs=('U401_vent', 'U401_recycled'),
                              gas=('CO2', 'NH3', 'O2'))

# Mixer crude ethanol beer
M402 = bst.units.Mixer('M402', ins=(R301-1, U401-1))
T401 = units.BeerTank('T401', ins=M402-0)

# Heat up crude beer by exchanging heat with stillage
H401 = bst.units.HXprocess('H401', ins=(T401-0, ''),
                           phase0='l', phase1='l', U=1.28)

# Remove solids from fermentation broth, based on the pressure filter in ref [1]
S401_index = [splits_df.index[0]] + splits_df.index[2:].to_list()
S401_cell_mass_split = [splits_df['stream_571'][0]] + splits_df['stream_571'][2:].to_list()
S401_filtrate_split = [splits_df['stream_535'][0]] + splits_df['stream_535'][2:].to_list()
# Moisture content is 35% in ref [1] but 25% in ref [2], used 35% to be conservative
S401 = units.CellMassFilter('S401', ins=H401-1, outs=('S401_cell_mass', 'S401_to_WWT'),
                            moisture_content=0.35,
                            split=find_split(S401_index,
                                             S401_cell_mass_split,
                                             S401_filtrate_split,
                                             chemical_groups))

# Beer column
xbot = convert_ethanol_wt_2_mol(0.00001)
ytop = convert_ethanol_wt_2_mol(0.5)
D401 = bst.units.BinaryDistillation('D401', ins=H401-0, k=1.25, Rmin=0.6,
                                    P=101325, y_top=ytop, x_bot=xbot,
                                    LHK=('Ethanol', 'Water'),
                                    tray_material='Stainless steel 304',
                                    vessel_material='Stainless steel 304')
D401.boiler.U = 1.85
D401_P = bst.units.Pump('D401_P', ins=D401-1, outs=1-H401)
D401_P.BM = 3.1

# Mix recycled ethanol
M403 = bst.units.Mixer('M403', ins=(D401-0, ''))

ytop = convert_ethanol_wt_2_mol(0.915)
D402 = bst.units.BinaryDistillation('D402', ins=M403-0, k=1.25, Rmin=0.6,
                                    P=101325, y_top=ytop, x_bot=xbot,
                                    LHK=('Ethanol', 'Water'),
                                    tray_material='Stainless steel 304',
                                    vessel_material='Stainless steel 304',
                                    is_divided=True)
D402.boiler.U = 1.85
D402_P = bst.units.Pump('D402_P', ins=D402-1, outs='D402_to_WWT')
D402_P.BM = 3.1

D402_H = bst.units.HXutility('D402_H', ins=D402-0, T=115+283.15, V=1)

# Molecular sieve, split based on streams 515 and 511 in ref [1]
split_ethanol = 1 - 21673/27022
split_water = 1 - 108/2164
S402 = bst.units.MolecularSieve('S402', ins=D402_H-0, outs=(1-M403, ''),
                                split=(split_ethanol, split_water),
                                order=('Ethanol', 'Water'))
# Condense ethanol product
S402_H = bst.units.HXutility('S402_H', ins=S402-1, outs='ethanol_to_storage',
                             V=0, T=350)


ethanol_purification_recycle = System('ethanol_purification_recycle',
                                      path=(M403, D402, D402_P, D402_H, S402, S402_H),
                                      recycle=S402-0)

ethanol_purification_sys = System('ethanol_purification_sys',
                                  path=(M401, U401, M402, T401, H401,
                                        D401, H401, D401_P, H401, S401,
                                        ethanol_purification_recycle))


# %%

# =============================================================================
# Wastewater treatment streams
# =============================================================================

caustic_R602 = Stream('caustic_R602', units='kg/hr')
polymer_R602 = Stream('polymer_R602', units='kg/hr', price=price['WWT polymer'])
air_R602 = Stream('air_R602', phase='g', units='kg/hr')

# =============================================================================
# Wastewater treatment units
# =============================================================================

# Mix all incoming wastewater streams, the last one reserved for blowdowns from CHP and CT
M601 = bst.units.Mixer('M601', ins=(D402_P-0, S401-1, ''))


R601 = units.AnaerobicDigestion('R601', ins=M601-0,
                                outs=('biogas', 'anaerobic_treated_water', 
                                      'anaerobic_sludge'),
                                reactants=soluble_organics,
                                split=find_split(splits_df.index,
                                                 splits_df['stream_611'],
                                                 splits_df['stream_612'],
                                                 chemical_groups),
                                T=35+273.15, COD_chemicals=COD_chemicals)

# Feedstock flow rate in dry U.S. ton per day
get_flow_tpd = lambda: (feedstock.F_mass-feedstock.imass['H2O'])*24/907.185
R602 = units.AerobicDigestion('R602', ins=(R601-1, '', caustic_R602, 'ammonia_R601',
                                           polymer_R602, air_R602),
                              outs=('aerobic_vent', 'aerobic_treated_water'),
                              reactants=soluble_organics,
                              # Stream 632 in ref [1], scaled based on feedstock loading
                              caustic_mass=2252*get_flow_tpd()/2205,
                              need_ammonia=False, COD_chemicals=COD_chemicals)

S601 = units.MembraneBioreactor('S601', ins=R602-1,
                                outs=('membrane_treated_water', 'membrane_sludge'),
                                split=find_split(splits_df.index,
                                                 splits_df['stream_624'],
                                                 splits_df['stream_625'],
                                                 chemical_groups),
                                COD_chemicals=COD_chemicals)

# Recycled sludge stream of memberane bioreactor, the majority of it (96%)
# goes to aerobic digestion based on ref [1]
S602 = bst.units.Splitter('S602', ins=S601-1, outs=('to_aerobic_digestion', ''), 
                          split=0.96)

S603 = units.BeltThickener('S603', ins=(R601-2, S602-1),
                           outs=('S603_centrate', 'S603_solids'),
                           COD_chemicals=COD_chemicals,
                           solubles=solubles, insolubles=insolubles)

# Ref [1] included polymer addition in process flow diagram, but did not include
# in the variable operating cost, thus followed ref [2] to add polymer in AerobicDigestion
S604 = units.SludgeCentrifuge('S604', ins=S603-1, outs=('S604_centrate',
                                                        'S604_to_CHP'),
                              COD_chemicals=COD_chemicals,
                              solubles=solubles, insolubles=insolubles)

# Mix recycles to aerobic digestion
M602 = bst.units.Mixer('M602', ins=(S602-0, S603-0, S604-0), outs=1-R602)

aerobic_digestion_recycle = System('aerobic_digestion_recycle',
                                   path=(R602, S601, S602, S603, S604, M602),
                                   recycle=M602-0)

S605 = units.ReverseOsmosis('S605', ins=S601-0, outs=('recycled_water', 'brine'))

wastewater_sys = System('wastewater_sys',
                        path=(M601, R601, aerobic_digestion_recycle, S605))


# %%

# =============================================================================
# Facilities streams
# =============================================================================

# For products
ethanol = Stream('ethanol', units='kg/hr', price=price['Ethanol'])
ethanol_extra = Stream('ethanol_extra', units='kg/hr')
denaturant = Stream('denaturant', units='kg/hr', price=price['Denaturant'])

# Process chemicals
caustic = Stream('caustic', units='kg/hr', price=price['NaOH'])
CSL = Stream('CSL', units='kg/hr', price=price['CSL'])
DAP = Stream('DAP', units='kg/hr', price=price['DAP'])
ammonia = Stream('ammonia', units='kg/hr', price=price['NH4OH'])
sulfuric_acid = Stream('sulfuric_acid', units='kg/hr', price=price['H2SO4'])

# Chemicals used/generated in CHP
lime_CHP = Stream('lime_CHP', units='kg/hr', price=price['Lime'])
# Scaled based on feedstock flow, 1054 from Table 33 in ref [2] as NH3
ammonia_CHP = Stream('ammonia_CHP', units='kg/hr',
                     NH4OH=1054*35.046/17.031*get_flow_tpd()/2205)
boiler_chems = Stream('boiler_chems', units='kg/hr', price=price['Boiler chems'])
baghouse_bag = Stream('baghouse_bag', units='kg/hr', price=price['Baghouse bag'])
# Supplementary natural gas for CHP if produced steam not enough for regenerating
# all steam streams required by the system
natural_gas = Stream('natural_gas', units='kg/hr', price=price['Natural gas'])
ash = Stream('ash', units='kg/hr', price=price['Ash disposal'])

cooling_tower_chems = Stream('cooling_tower_chems', units='kg/hr',
                             price=price['Cooling tower chems'])

system_makeup_water = Stream('system_makeup_water', units='kg/hr',
                             price=price['Makeup water'])

# 8021 based on stream 713 in Humbird et al.
firewater_in = Stream('firewater_in', 
                       Water=8021*get_flow_tpd()/2205, units='kg/hr')

# # Clean-in-place, 145 based on equipment M-910 (clean-in-place system) in ref [1]
CIP_chems_in = Stream('CIP_chems_in', Water=145*get_flow_tpd()/2205, 
                      units='kg/hr')

# 1372608 based on stream 950 in ref [1]
# Air needed for multiple processes (including enzyme production that was not included here),
# not rigorously modeled, only scaled based on plant size
plant_air_in = Stream('plant_air_in', phase='g', units='kg/hr',
                      N2=0.79*1372608*get_flow_tpd()/2205,
                      O2=0.21*1372608*get_flow_tpd()/2205)

# =============================================================================
# Facilities units
# =============================================================================

# Pure ethanol
T701 = units.EthanolStorage('T701', ins=S402_H-0)
T702 = units.DenaturantStorage('T702', ins=denaturant)

# Mix in denaturant for final ethanol product
M701 = units.DenaturantMixer('M701', ins=(T701-0, T702-0), outs=ethanol)

# T703 = units.SulfuricAcidStorage('T703', ins=sulfuric_acid, outs=sulfuric_acid_T201)

T704 = units.AmmoniaStorage('T704', ins=ammonia)
T704_S = bst.units.ReversedSplitter('T704_S', ins=T704-0, 
                                    outs=('', ammonia_CHP))

T705 = units.CausticStorage('T705', ins=caustic, outs=caustic_R602)

T706 = units.CSLstorage('T706', ins=CSL)
T706_S = bst.units.ReversedSplitter('T706_S', ins=T706-0, outs=(CSL_R301, CSL_R302))

T707 = units.DAPstorage('T707', ins=DAP)
T707_S = bst.units.ReversedSplitter('T707_S', ins=T707-0, outs=(DAP_R301, DAP_R302))

T708 = units.FirewaterStorage('T708', ins=firewater_in, outs='firewater_out')

# Mix solids for CHP
M702 = bst.units.Mixer('M702', ins=(S401-0, S604-1), outs='wastes_to_CHP')

CHP = facilities.CHP('CHP', ins=(M702-0, R601-0, lime_CHP, ammonia_CHP, boiler_chems,
                                 baghouse_bag, natural_gas, 'boiler_feed_water'),
                     B_eff=0.8, TG_eff=0.85, combustibles=combustibles,
                     outs=('gas_emission', ash, 'boiler_blowdown_water'))

CT = facilities.CT('CT', ins=('return_cooling_water', cooling_tower_chems,
                              'CT_makeup_water'),
                   outs=('process_cooling_water', 'cooling_tower_blowdown'))

CWP = facilities.CWP('CWP', ins='return_chilled_water',
                     outs='process_chilled_water')

BDM = bst.units.BlowdownMixer('BDM',ins=(CHP.outs[-1], CT.outs[-1]),
                              outs=M601.ins[-1])

# All water consumed by the system
process_water_streams = {
    # 'pretreatment': (water_M201, water_M202, steam_M203, water_M205),
    'conversion': (water_M301,),
    'separation': (water_U401,),
    'facilities': (CHP.ins[-1], CT.ins[-1])
    }

PWC = facilities.PWC('PWC', ins=(system_makeup_water, S605-0), 
                     process_water_streams=sum(process_water_streams.values(), ()),
                     recycled_blowdown_streams=None,
                     outs=('process_water', 'discharged_water'))

ADP = facilities.ADP('ADP', ins=plant_air_in, outs='plant_air_out',
                     ratio=get_flow_tpd()/2205)
CIP = facilities.CIP('CIP', ins=CIP_chems_in, outs='CIP_chems_out')


# %%

# =============================================================================
# Complete system
# =============================================================================

ethanol_sys = System('ethanol_sys',
                     path=(fermentation_sys,
                           ethanol_purification_sys, wastewater_sys,
                           T701, T702, M701,
                           # T703,
                           T704_S, T704,
                           T705, T706_S, T706, T707_S, T707, T708, M702),
                     facilities=(CHP, CT, CWP, PWC, ADP, CIP, BDM),
                     facility_recycle=BDM-0)

CHP_sys = System('CHP_sys', path=(CHP,))


# =============================================================================
# Techno-economic analysis (TEA)
# =============================================================================

auom = tmo.units_of_measure.AbsoluteUnitsOfMeasure
_ethanol_V = chems.Ethanol.V('l', 298.15, 101325) # molar volume in m3/mol	
_ethanol_MW = chems.Ethanol.MW
_ethanol_kg_2_gal = auom('gal').conversion_factor('liter')/_ethanol_V*_ethanol_MW/1e6
_feedstock_factor = auom('ton').conversion_factor('kg') / (1-0.2)

ISBL_units = set((*fermentation_sys.units,
                  *ethanol_purification_sys.units))
OSBL_units = list(ethanol_sys.units.difference(ISBL_units))

# CHP is not included in this TEA
OSBL_units.remove(CHP)
# biosteam Splitters and Mixers have no cost
for i in OSBL_units:
    if i.__class__ == bst.units.Mixer or i.__class__ == bst.units.Splitter:
        OSBL_units.remove(i)

ethanol_no_CHP_tea = ethanol_adipic_TEA(
        system=ethanol_sys, IRR=0.10, duration=(2016, 2046),
        depreciation='MACRS7', income_tax=0.21, operating_days=0.96*365,
        lang_factor=None, construction_schedule=(0.08, 0.60, 0.32),
        startup_months=3, startup_FOCfrac=1, startup_salesfrac=0.5,
        startup_VOCfrac=0.75, WC_over_FCI=0.05,
        finance_interest=0.08, finance_years=10, finance_fraction=0.4,
        OSBL_units=OSBL_units,
        warehouse=0.04, site_development=0.09, additional_piping=0.045,
        proratable_costs=0.10, field_expenses=0.10, construction=0.20,
        contingency=0.10, other_indirect_costs=0.10, 
        labor_cost=3212962*get_flow_tpd()/2205,
        labor_burden=0.90, property_insurance=0.007, maintenance=0.03)

# Removes units, feeds, and products of CHP_sys to avoid double-counting
ethanol_no_CHP_tea.units.remove(CHP)

for i in CHP_sys.feeds:
    ethanol_sys.feeds.remove(i)
for i in CHP_sys.products:
    ethanol_sys.products.remove(i)

# Changed to MACRS 20 to be consistent with ref [1]
CHP_tea = bst.TEA.like(CHP_sys, ethanol_no_CHP_tea)
CHP_tea.labor_cost = 0
CHP_tea.depreciation = 'MACRS20'
CHP_tea.OSBL_units = (CHP,)

ethanol_tea = bst.CombinedTEA([ethanol_no_CHP_tea, CHP_tea], IRR=0.10)
ethanol_sys._TEA = ethanol_tea

# =============================================================================
# Life cycle assessment (LCA)
# =============================================================================

LCA_streams = set([i for i in ethanol_sys.feeds if i.price]+ \
    [i for i in CHP_sys.feeds if i.price])
LCA_stream = Stream('LCA_stream', units='kg/hr')
    
def get_material_GWP():
    LCA_stream.mass = sum(i.mass for i in LCA_streams)
    chemical_GWP = LCA_stream.mass*GWP_CF_stream.mass
    return chemical_GWP.sum()/(ethanol.F_mass/_ethanol_kg_2_gal)

# GWP from onsite emission (e.g., combustion) of non-biogenic carbons
get_onsite_GWP = lambda: natural_gas.get_atomic_flow('C')*chems.CO2.MW \
    / (ethanol.F_mass/_ethanol_kg_2_gal)

# GWP from electricity
get_electricity_use = lambda: sum(i.power_utility.rate for i in ethanol_sys.units)
get_electricity_GWP = lambda: get_electricity_use()*GWP_CFs['Electricity'] \
    / (ethanol.F_mass/_ethanol_kg_2_gal)

get_GWP = lambda: get_material_GWP()+get_onsite_GWP()+get_electricity_GWP()


# %%

# =============================================================================
# Simulate system and get results
# =============================================================================

def simulate_get_MESP(feedstock_price=71.3):
    ethanol.price = 0
    ethanol_sys.simulate()
    # feedstock.price = feedstock_price / _feedstock_factor
    for i in range(3):
        ethanol.price = ethanol_tea.solve_price(ethanol)
    MESP = ethanol.price * _ethanol_kg_2_gal
    return MESP

# def simulate_get_MFPP(ethanol_price=2.2):
#     ethanol_sys.simulate()
#     ethanol.price = ethanol_price / _ethanol_kg_2_gal
#     for i in range(3):
#         MFPP = ethanol_tea.solve_price(feedstock)
#     MFPP *= _feedstock_factor
#     return MFPP

def simulate_and_print():
    MESP = simulate_get_MESP()
    print(f'AFEX MESP: ${MESP:.2f}/gal with default pretreatment efficacy')
    # print(f'GWP is {get_GWP():.3f} kg CO2-eq/gal ethanol')

# simulate_and_print()






















