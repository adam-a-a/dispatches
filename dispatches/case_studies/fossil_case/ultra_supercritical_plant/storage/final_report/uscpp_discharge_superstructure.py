#################################################################################
# DISPATCHES was produced under the DOE Design Integration and Synthesis Platform
# to Advance Tightly Coupled Hybrid Energy Systems program (DISPATCHES), and is
# copyright (c) 2020-2023 by the software owners: The Regents of the University
# of California, through Lawrence Berkeley National Laboratory, National
# Technology & Engineering Solutions of Sandia, LLC, Alliance for Sustainable
# Energy, LLC, Battelle Energy Alliance, LLC, University of Notre Dame du Lac, et
# al. All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. Both files are also available online at the URL:
# "https://github.com/gmlc-dispatches/dispatches".
#################################################################################

"""This is a Generalized Disjunctive Programming model for the
conceptual design of an ultra supercritical coal-fired power plant
integrated with a discharge storage system

"""

__author__ = "Naresh Susarla and Soraya Rawlings"

import logging

# Import Python libraries
from math import pi
from IPython import embed
import csv

# Import Pyomo libraries
import pyomo.environ as pyo
from pyomo.environ import (Block, Param, Constraint, Objective,
                           TransformationFactory, SolverFactory,
                           Expression, value, log, exp, Var, maximize)
from pyomo.network import Arc
from pyomo.util.calc_var_value import calculate_variable_from_constraint
from pyomo.gdp import Disjunct, Disjunction
from pyomo.network.plugins import expand_arcs
from pyomo.contrib.fbbt.fbbt import  _prop_bnds_root_to_leaf_map
from pyomo.core.expr.numeric_expr import ExternalFunctionExpression

# Import IDAES libraries
import idaes.logger as idaeslog
import idaes.core.util.scaling as iscale
from idaes.core.util.initialization import propagate_state
from idaes.core.solvers.get_solver import get_solver
from idaes.core.util.model_statistics import degrees_of_freedom
from idaes.core import UnitModelCostingBlock
from idaes.models.unit_models import HeatExchanger
from idaes.models.unit_models.heat_exchanger import delta_temperature_underwood_callback
from idaes.models_extra.power_generation.unit_models.helm import (HelmTurbineStage,
                                                                  HelmSplitter)
from idaes.models.costing.SSLW import (
    SSLWCosting,
    SSLWCostingData
)
from idaes.core.util.exceptions import ConfigurationError
# Import ultra supercritical power plant model
from dispatches.case_studies.fossil_case.ultra_supercritical_plant import (
    ultra_supercritical_powerplant as usc)

# Import properties package for Solar salt
from dispatches.properties import solarsalt_properties
logging.getLogger('pyomo.repn.plugins.nl_writer').setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO)


scaling_obj = 1e-1
save_csv = True

print("Using scaling_obj={}".format(scaling_obj))

def create_discharge_model(m, add_efficiency=None, power_max=None):
    """Create flowsheet and add unit models.

    """

    # Create a block to add charge storage model
    m.fs.discharge = Block()

    # Add model data
    _add_data(m)

    # Add Solar salt properties
    m.fs.solar_salt_properties = solarsalt_properties.SolarsaltParameterBlock()

    ###########################################################################
    #  Add unit models
    ###########################################################################

    # Declare splitter to divert condensate to discharge storage heat
    # exchanger
    m.fs.discharge.es_split = HelmSplitter(
        property_package=m.fs.prop_water,
        outlet_list=["to_fwh", "to_hxd"],
    )

    # Declare discharge storage heat exchanger
    m.fs.discharge.hxd = HeatExchanger(
        delta_temperature_callback=delta_temperature_underwood_callback,
        hot_side_name="shell",
        cold_side_name="tube",
        shell={"property_package": m.fs.solar_salt_properties},
        tube={"property_package": m.fs.prop_water},
    )

    # Declare turbine for storage system
    m.fs.discharge.es_turbine = HelmTurbineStage(
        property_package=m.fs.prop_water
    )

    ###########################################################################
    #  Declare disjuncts
    ###########################################################################
    # Disjunction 1 for the sink of discharge HX consists of 2 disjuncts:
    #   1. condpump_source_disjunct ======> condensate from condenser pump
    #   2. fwh1_source_disjunct     ======> condensate from feed water heater 1
    #   3. fwh2_source_disjunct     ======> condensate from feed water heater 2
    #   4. fwh3_source_disjunct     ======> condensate from feed water heater 3
    #   5. fwh4_source_disjunct     ======> condensate from feed water heater 4
    #   6. fwh4_source_disjunct     ======> condensate from feed water heater 5
    #   7. booster_source_disjunct  ======> condensate from booster pump
    #   8. bfp_source_disjunct      ======> condensate from boiler feed water pump
    #   9. fwh6_source_disjunct     ======> condensate from feed water heater 6
    #   10. fwh8_source_disjunct     ======> condensate from feed water heater 8
    #   11. fwh9_source_disjunct     ======> condensate from feed water heater 9

    # Declare disjuncts in disjunction 1
    m.fs.discharge.condpump_source_disjunct = Disjunct(
        rule=condpump_source_disjunct_equations)
    m.fs.discharge.fwh1_source_disjunct = Disjunct(
        rule=fwh1_source_disjunct_equations)
    m.fs.discharge.fwh2_source_disjunct = Disjunct(
        rule=fwh2_source_disjunct_equations)
    m.fs.discharge.fwh3_source_disjunct = Disjunct(
        rule=fwh3_source_disjunct_equations)
    m.fs.discharge.fwh4_source_disjunct = Disjunct(
        rule=fwh4_source_disjunct_equations)
    m.fs.discharge.fwh5_source_disjunct = Disjunct(
        rule=fwh5_source_disjunct_equations)
    m.fs.discharge.booster_source_disjunct = Disjunct(
        rule=booster_source_disjunct_equations)
    m.fs.discharge.bfp_source_disjunct = Disjunct(
        rule=bfp_source_disjunct_equations)
    m.fs.discharge.fwh6_source_disjunct = Disjunct(
        rule=fwh6_source_disjunct_equations)
    m.fs.discharge.fwh8_source_disjunct = Disjunct(
        rule=fwh8_source_disjunct_equations)
    m.fs.discharge.fwh9_source_disjunct = Disjunct(
        rule=fwh9_source_disjunct_equations)

    ###########################################################################
    # Add constraints
    ###########################################################################
    _make_constraints(m, add_efficiency=add_efficiency, power_max=power_max)

    _solar_salt_ohtc_calculation(m)

    ###########################################################################
    #  Create stream arcs
    ###########################################################################

    _create_arcs(m)
    TransformationFactory("network.expand_arcs").apply_to(m.fs.discharge)

    return m


def _add_data(m):
    """Add data to the model
    """

    # Add Chemical Engineering cost index for 2019
    m.CE_index = 607.5

    # Add operating hours
    m.fs.discharge.hours_per_day = pyo.Param(
        initialize=6,
        doc='Number of hours of charging per day'
    )

    # Define number of years over which the costs are annualized
    m.fs.discharge.num_of_years = pyo.Param(
        initialize=30,
        doc='Number of years for cost annualization')

    # Add data to compute overall heat transfer coefficient for the
    # Solar salt storage heat exchanger using the Sieder-Tate
    # correlation. Parameters for tube diameter and thickness assumed
    # from the data in (2017) He et al., Energy Procedia 105, 980-985
    m.fs.discharge.data_hxd = {
        'tube_inner_dia': 0.032,
        'tube_outer_dia': 0.036,
        'k_steel': 21.5,
        'number_tubes': 20,
        'shell_inner_dia': 1
    }
    m.fs.discharge.hxd_tube_inner_dia = pyo.Param(
        initialize=m.fs.discharge.data_hxd['tube_inner_dia'],
        doc='Tube inner diameter in m')
    m.fs.discharge.hxd_tube_outer_dia = pyo.Param(
        initialize=m.fs.discharge.data_hxd['tube_outer_dia'],
        doc='Tube outer diameter in m')
    m.fs.discharge.hxd_k_steel = pyo.Param(
        initialize=m.fs.discharge.data_hxd['k_steel'],
        doc='Thermal conductivity of steel in W/m.K')
    m.fs.discharge.hxd_n_tubes = pyo.Param(
        initialize=m.fs.discharge.data_hxd['number_tubes'],
        doc='Number of tubes')
    m.fs.discharge.hxd_shell_inner_dia = pyo.Param(
        initialize=m.fs.discharge.data_hxd['shell_inner_dia'],
        doc='Shell inner diameter in m')

    # Calculate sectional area of storage heat exchanger
    m.fs.discharge.hxd_tube_cs_area = pyo.Expression(
        expr=(pi / 4) *
        (m.fs.discharge.hxd_tube_inner_dia**2),
        doc="Tube inside cross sectional area in m2")
    m.fs.discharge.hxd_tube_out_area = pyo.Expression(
        expr=(pi / 4) *
        (m.fs.discharge.hxd_tube_outer_dia**2),
        doc="Tube cross sectional area including thickness in m2")
    m.fs.discharge.hxd_shell_eff_area = pyo.Expression(
        expr=(
            (pi / 4) *
            m.fs.discharge.hxd_shell_inner_dia**2 -
            m.fs.discharge.hxd_n_tubes *
            m.fs.discharge.hxd_tube_out_area
        ),
        doc="Effective shell cross sectional area in m2")

    m.fs.discharge.hxd_tube_dia_ratio = (
        m.fs.discharge.hxd_tube_outer_dia / m.fs.discharge.hxd_tube_inner_dia)
    m.fs.discharge.hxd_log_tube_dia_ratio = log(m.fs.discharge.hxd_tube_dia_ratio)

    # Add fuel cost data
    m.data_cost = {
        'coal_price': 2.11e-9,
        'solar_salt_price': 0.49,
    }
    m.fs.discharge.coal_price = pyo.Param(
        initialize=m.data_cost['coal_price'],
        doc='Coal price based on HHV in $/J')

    m.fs.discharge.solar_salt_price = pyo.Param(
        initialize=m.data_cost['solar_salt_price'],
        doc='Solar salt price in $/kg')

    m.fs.discharge.salt_amount = pyo.Param(
        initialize=6840497.79092901,
        doc='Salt amount fixed from charge model'
    )
    # Add parameters to calculate the Solar salt pump costing. Since
    # the unit is not explicitly modeled, the IDAES cost method is not
    # used for this equipment.  The primary purpose of the salt pump
    # is to move the molten salt without changing the pressure. Thus,
    # the pressure head is computed assuming that the salt is moved on
    # an average of 5m linear distance.
    m.data_salt_pump = {
        'FT': 1.5,
        'FM': 2.0,
        'head': 3.281*5,
        'motor_FT': 1,
        'nm': 1
    }
    m.fs.discharge.spump_FT = pyo.Param(
        initialize=m.data_salt_pump['FT'],
        doc='Pump Type Factor for vertical split case')
    m.fs.discharge.spump_FM = pyo.Param(
        initialize=m.data_salt_pump['FM'],
        doc='Pump Material Factor Stainless Steel')
    m.fs.discharge.spump_head = pyo.Param(
        initialize=m.data_salt_pump['head'],
        doc='Pump Head 5m in ft.')
    m.fs.discharge.spump_motorFT = pyo.Param(
        initialize=m.data_salt_pump['motor_FT'],
        doc='Motor Shaft Type Factor')
    m.fs.discharge.spump_nm = pyo.Param(
        initialize=m.data_salt_pump['nm'],
        doc='Motor Shaft Type Factor')


def _make_constraints(m, add_efficiency=None, power_max=None):
    """Declare constraints for the discharge model

    """

    @m.fs.discharge.es_turbine.Constraint(
        m.fs.time,
        doc="Turbine outlet should be a saturated steam")
    def constraint_esturbine_temperature_out(b, t):
        return (
            b.control_volume.properties_out[t].temperature >=
            b.control_volume.properties_out[t].temperature_sat + 1
        )

    # Add a constraint to storage turbine to ensure that the inlet
    # to the turbine should be superheated steam
    @m.fs.discharge.es_turbine.Constraint(
        m.fs.time,
        doc="Turbine inlet should be superheated steam")
    def constraint_esturbine_temperature_in(b, t):
        return (
            b.control_volume.properties_in[t].temperature >=
            b.control_volume.properties_in[t].temperature_sat + 1
        )

    m.fs.net_power = pyo.Expression(
        expr=(m.fs.plant_power_out[0]
              + (m.fs.discharge.es_turbine.control_volume.work[0] * (-1e-6)))
    )

    m.fs.boiler_efficiency = pyo.Var(initialize=0.9,
                                     bounds=(0, 1),
                                     doc="Boiler efficiency")
    m.fs.boiler_efficiency_eq = pyo.Constraint(
        expr=m.fs.boiler_efficiency == (
            0.2143 *
            (m.fs.net_power / power_max) +
            0.7357
        ),
        doc="Boiler efficiency in fraction"
    )
    m.fs.coal_heat_duty = pyo.Var(
        initialize=1000,
        bounds=(0, 1e5),
        doc="Coal heat duty supplied to boiler in MW")

    if add_efficiency:
        m.fs.coal_heat_duty_eq = pyo.Constraint(
            expr=m.fs.coal_heat_duty *
            m.fs.boiler_efficiency ==
            m.fs.plant_heat_duty[0]
        )
    else:
        m.fs.coal_heat_duty_eq = pyo.Constraint(
            expr=m.fs.coal_heat_duty == m.fs.plant_heat_duty[0]
        )

    m.fs.cycle_efficiency = pyo.Var(initialize=0.4,
                                    bounds=(0, 1),
                                    doc="Cycle efficiency")
    m.fs.cycle_efficiency_eq = pyo.Constraint(
        expr=(
            m.fs.cycle_efficiency *
            m.fs.coal_heat_duty
        ) == m.fs.net_power,
        doc="Cycle efficiency"
    )


def _solar_salt_ohtc_calculation(m):
    """Block of equations to compute overall heat transfer coefficient for
    Solar salt heat exchanger

    """

    # Calculate Reynolds number for the salt
    m.fs.discharge.hxd.salt_reynolds_number = pyo.Expression(
        expr=(
            (m.fs.discharge.hxd.shell_inlet.flow_mass[0] *
             m.fs.discharge.hxd_tube_outer_dia) /
            (m.fs.discharge.hxd_shell_eff_area *
             m.fs.discharge.hxd.hot_side.properties_in[0].visc_d_phase["Liq"])
        ),
        doc="Salt Reynolds Number")

    # Calculate Prandtl number for the salt
    m.fs.discharge.hxd.salt_prandtl_number = pyo.Expression(
        expr=(
            m.fs.discharge.hxd.hot_side.properties_in[0].cp_mass["Liq"] *
            m.fs.discharge.hxd.hot_side.properties_in[0].visc_d_phase["Liq"] /
            m.fs.discharge.hxd.hot_side.properties_in[0].therm_cond_phase["Liq"]
        ),
        doc="Salt Prandtl Number")

    # Calculate Prandtl Wall number for the salt
    m.fs.discharge.hxd.salt_prandtl_wall = pyo.Expression(
        expr=(
            m.fs.discharge.hxd.hot_side.properties_out[0].cp_mass["Liq"] *
            m.fs.discharge.hxd.hot_side.properties_out[0].visc_d_phase["Liq"] /
            m.fs.discharge.hxd.hot_side.properties_out[0].therm_cond_phase["Liq"]
        ),
        doc="Salt Prandtl Number at wall")

    # Calculate Nusselt number for the salt
    m.fs.discharge.hxd.salt_nusselt_number = pyo.Expression(
        expr=(
            0.35 *
            (m.fs.discharge.hxd.salt_reynolds_number**0.6) *
            (m.fs.discharge.hxd.salt_prandtl_number**0.4) *
            ((m.fs.discharge.hxd.salt_prandtl_number /
              m.fs.discharge.hxd.salt_prandtl_wall) ** 0.25) *
            (2**0.2)
        ),
        doc="Salt Nusslet Number from 2019, App Ener (233-234), 126")

    # Calculate Reynolds number for the steam
    m.fs.discharge.hxd.steam_reynolds_number = pyo.Expression(
        expr=(
            m.fs.discharge.hxd.tube_inlet.flow_mol[0] *
            m.fs.discharge.hxd.cold_side.properties_in[0].mw *
            m.fs.discharge.hxd_tube_inner_dia /
            (m.fs.discharge.hxd_tube_cs_area *
             m.fs.discharge.hxd_n_tubes *
             m.fs.discharge.hxd.cold_side.properties_in[0].visc_d_phase["Liq"])
        ),
        doc="Steam Reynolds Number")

    # Calculate Reynolds number for the steam
    m.fs.discharge.hxd.steam_prandtl_number = pyo.Expression(
        expr=(
            (m.fs.discharge.hxd.cold_side.properties_in[0].cp_mol /
             m.fs.discharge.hxd.cold_side.properties_in[0].mw) *
            m.fs.discharge.hxd.cold_side.
            properties_in[0].visc_d_phase["Liq"] /
            m.fs.discharge.hxd.cold_side.properties_in[0].therm_cond_phase["Liq"]
        ),
        doc="Steam Prandtl Number")

    # Calculate Reynolds number for the steam
    m.fs.discharge.hxd.steam_nusselt_number = pyo.Expression(
        expr=(
            0.023 *
            (m.fs.discharge.hxd.steam_reynolds_number**0.8) *
            (m.fs.discharge.hxd.steam_prandtl_number**(0.33)) *
            (
                (m.fs.discharge.hxd.cold_side.properties_in[0].visc_d_phase["Liq"] /
                 m.fs.discharge.hxd.cold_side.properties_out[0].visc_d_phase["Vap"]) ** 0.14
            )
        ),
        doc="Steam Nusslet Number from 2001 Zavoico, Sandia")

    # Calculate heat transfer coefficients for the salt and steam
    # sides of discharge heat exchanger
    m.fs.discharge.hxd.h_salt = pyo.Expression(
        expr=(
            m.fs.discharge.hxd.hot_side.properties_in[0].therm_cond_phase["Liq"] *
            m.fs.discharge.hxd.salt_nusselt_number /
            m.fs.discharge.hxd_tube_outer_dia
        ),
        doc="Salt side convective heat transfer coefficient in W/m.K")
    m.fs.discharge.hxd.h_steam = pyo.Expression(
        expr=(
            m.fs.discharge.hxd.cold_side.properties_in[0].therm_cond_phase["Liq"] *
            m.fs.discharge.hxd.steam_nusselt_number /
            m.fs.discharge.hxd_tube_inner_dia
        ),
        doc="Steam side convective heat transfer coefficient in W/m.K")

    # Calculate overall heat transfer coefficient for Solar salt heat
    # exchanger
    @m.fs.discharge.hxd.Constraint(m.fs.time)
    def constraint_hxd_ohtc(b, t):
        return (
            m.fs.discharge.hxd.overall_heat_transfer_coefficient[t] * (
                2 * m.fs.discharge.hxd_k_steel *
                m.fs.discharge.hxd.h_steam +
                m.fs.discharge.hxd_tube_outer_dia *
                m.fs.discharge.hxd_log_tube_dia_ratio *
                m.fs.discharge.hxd.h_salt *
                m.fs.discharge.hxd.h_steam +
                m.fs.discharge.hxd_tube_dia_ratio *
                m.fs.discharge.hxd.h_salt *
                2 * m.fs.discharge.hxd_k_steel
            )
        ) == (2 * m.fs.discharge.hxd_k_steel *
              m.fs.discharge.hxd.h_salt *
              m.fs.discharge.hxd.h_steam)


def _disconnect_arcs(m):
    """Disconnect arcs from ultra-supercritical plant base model to
    connect the Solar salt discharge storage system

    """

    for arc_s in [
            m.fs.condpump_to_fwh1,
            m.fs.fwh1_to_fwh2,
            m.fs.fwh2_to_fwh3,
            m.fs.fwh3_to_fwh4,
            m.fs.fwh4_to_fwh5,
            m.fs.fwh5_to_deaerator,
            m.fs.booster_to_fwh6,
            m.fs.fwh6_to_fwh7,
            m.fs.bfp_to_fwh8,
            m.fs.fwh8_to_fwh9,
            m.fs.fwh9_to_boiler
            ]:
        arc_s.expanded_block.enth_mol_equality.deactivate()
        arc_s.expanded_block.flow_mol_equality.deactivate()
        arc_s.expanded_block.pressure_equality.deactivate()


def _create_arcs(m):
    """Create arcs to connect the discharge storage system to the power
    plant

    """

    # Disconnect arcs to include charge storage system
    _disconnect_arcs(m)

    m.fs.discharge.essplit_to_hxd = Arc(
        source=m.fs.discharge.es_split.to_hxd,
        destination=m.fs.discharge.hxd.tube_inlet,
        doc="Connection from ES splitter to HXD"
    )
    m.fs.discharge.hxd_to_esturbine = Arc(
        source=m.fs.discharge.hxd.tube_outlet,
        destination=m.fs.discharge.es_turbine.inlet,
        doc="Connection from HXD to ES turbine"
    )


def add_disjunction(m):
    """Add disjunction for the selection of condensate source to integrate
    the discharge storage system to the power plant model

    """

    # Add disjunction 1 for condensate source selection
    m.fs.hxd_source_disjunction = Disjunction(
        expr=[
            m.fs.discharge.booster_source_disjunct,
            m.fs.discharge.fwh1_source_disjunct,
            m.fs.discharge.fwh2_source_disjunct,
            m.fs.discharge.fwh3_source_disjunct,
            m.fs.discharge.bfp_source_disjunct,
            m.fs.discharge.fwh4_source_disjunct,
            m.fs.discharge.fwh5_source_disjunct,
            m.fs.discharge.fwh6_source_disjunct,
            m.fs.discharge.fwh8_source_disjunct,
            m.fs.discharge.fwh9_source_disjunct,
            m.fs.discharge.condpump_source_disjunct
            ]
    )

    # Expand arcs within the disjuncts
    expand_arcs.obj_iter_kwds['descend_into'] = (Block, Disjunct)
    TransformationFactory("network.expand_arcs").apply_to(m.fs.discharge)

    return m


def condpump_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from Condensate Pump

    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.condpump_source_disjunct.condpump_to_essplit = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from Condenser pump to ES splitter"
    )
    m.fs.discharge.condpump_source_disjunct.essplit_to_fwh1 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from ES splitter to FWH1"
    )

    m.fs.discharge.condpump_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.condpump_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.condpump_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.condpump_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.condpump_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.condpump_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.condpump_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.condpump_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.condpump_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.condpump_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet,
        doc="Connection from FWH9 to boiler"
    )


def fwh5_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH5
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh5_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh5_to_essplit = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH5 to ES splitter"
    )
    m.fs.discharge.fwh5_source_disjunct.essplit_to_deaerator = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from ES splitter to Deaerator"
    )

    m.fs.discharge.fwh5_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh5_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh5_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet
    )


def fwh1_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH1
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh1_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh1_to_essplit = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH1 to ES splitter"
    )
    m.fs.discharge.fwh1_source_disjunct.essplit_to_fwh2 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from ES splitter to FWH2"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh1_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster to FWH6"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh1_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh1_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet,
        doc="Connection from FWH9 to boiler"
    )


def fwh2_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH2
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh2_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh2_to_essplit = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH2 to ES splitter"
    )
    m.fs.discharge.fwh2_source_disjunct.essplit_to_fwh3 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from ES splitter to FWH3"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh2_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh2_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh2_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet
    )


def fwh3_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH3
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh3_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet
    )

    m.fs.discharge.fwh3_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh3_to_essplit = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH3 to ES splitter"
    )
    m.fs.discharge.fwh3_source_disjunct.essplit_to_fwh4 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from ES splitter to FWH4"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh3_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh3_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh3_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet
    )

def fwh4_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH4
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh4_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh4_to_essplit = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH4 to ES splitter"
    )
    m.fs.discharge.fwh4_source_disjunct.essplit_to_fwh5 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from ES splitter to FWH5"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh4_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh4_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh4_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet,
        doc="Connection from FWH9 to boiler"
    )


def booster_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from Booster Pump
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.booster_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.booster_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.booster_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.booster_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.booster_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.booster_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.booster_source_disjunct.booster_to_essplit = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from Booster pump to ES splitter"
    )
    m.fs.discharge.booster_source_disjunct.essplit_to_fwh6 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from ES splitter to FWH6"
    )

    m.fs.discharge.booster_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.booster_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.booster_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.booster_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet,
        doc="Connection from FWH9 to boiler"
    )


def fwh6_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH6
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh6_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh6_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh6_to_essplit = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH6 to ES splitter"
    )
    m.fs.discharge.fwh6_source_disjunct.essplit_to_fwh7 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from ES splitter to FWH7"
    )

    m.fs.discharge.fwh6_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh6_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet
    )


def bfp_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from BFP pump
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.bfp_source_disjunct.bfp_to_essplit = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from BFP to ES splitter"
    )
    m.fs.discharge.bfp_source_disjunct.essplit_to_fwh8 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from ES splitter to FWH8"
    )

    m.fs.discharge.bfp_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.bfp_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.bfp_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.bfp_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.bfp_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.bfp_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.bfp_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.bfp_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )
    m.fs.discharge.bfp_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )
    m.fs.discharge.bfp_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet,
        doc="Connection from FWH9 to boiler"
    )


def fwh8_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH8
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh8_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh8_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster pump to FWH6"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh8_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh8_to_essplit = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH8 to ES splitter"
    )
    m.fs.discharge.fwh8_source_disjunct.essplit_to_fwh9 = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from ES splitter to FWH9"
    )

    m.fs.discharge.fwh8_source_disjunct.fwh9_to_boiler = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.boiler.inlet
    )


def fwh9_source_disjunct_equations(disj):
    """Disjunction 1: Water is sourced from FWH9
    """

    m = disj.model()

    # Define arcs to connect units within disjunct
    m.fs.discharge.fwh9_source_disjunct.fwh9_to_essplit = Arc(
        source=m.fs.fwh[9].tube_outlet,
        destination=m.fs.discharge.es_split.inlet,
        doc="Connection from FWH9 to the ES SPlitter"
    )
    m.fs.discharge.fwh9_source_disjunct.essplit_to_boiler = Arc(
        source=m.fs.discharge.es_split.to_fwh,
        destination=m.fs.boiler.inlet,
        doc="Connection from ES splitter to Boiler"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh4_to_fwh5 = Arc(
        source=m.fs.fwh[4].tube_outlet,
        destination=m.fs.fwh[5].tube_inlet,
        doc="Connection from FWH4 to FWH5"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh5_to_deaerator = Arc(
        source=m.fs.fwh[5].tube_outlet,
        destination=m.fs.deaerator.feedwater,
        doc="Connection from FWH5 to deaerator"
    )

    m.fs.discharge.fwh9_source_disjunct.condpump_to_fwh1 = Arc(
        source=m.fs.cond_pump.outlet,
        destination=m.fs.fwh[1].tube_inlet,
        doc="Connection from condenser pump to FWH1"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh1_to_fwh2 = Arc(
        source=m.fs.fwh[1].tube_outlet,
        destination=m.fs.fwh[2].tube_inlet,
        doc="Connection from FWH1 to FWH2"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh2_to_fwh3 = Arc(
        source=m.fs.fwh[2].tube_outlet,
        destination=m.fs.fwh[3].tube_inlet,
        doc="Connection from FWH2 to FWH3"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh3_to_fwh4 = Arc(
        source=m.fs.fwh[3].tube_outlet,
        destination=m.fs.fwh[4].tube_inlet,
        doc="Connection from FWH3 to FWH4"
    )

    m.fs.discharge.fwh9_source_disjunct.booster_to_fwh6 = Arc(
        source=m.fs.booster.outlet,
        destination=m.fs.fwh[6].tube_inlet,
        doc="Connection from booster to FWH6"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh6_to_fwh7 = Arc(
        source=m.fs.fwh[6].tube_outlet,
        destination=m.fs.fwh[7].tube_inlet,
        doc="Connection from FWH6 to FWH7"
    )

    m.fs.discharge.fwh9_source_disjunct.fwh8_to_fwh9 = Arc(
        source=m.fs.fwh[8].tube_outlet,
        destination=m.fs.fwh[9].tube_inlet,
        doc="Connection from FWH8 to FWH9"
    )

    m.fs.discharge.fwh9_source_disjunct.bfp_to_fwh8 = Arc(
        source=m.fs.bfp.outlet,
        destination=m.fs.fwh[8].tube_inlet,
        doc="Connection from BFP to FWH8"
    )


def set_model_input(m):
    """Define model inputs such as fixed variables and parameter
    values. The arameter values in this block, unless otherwise stated
    explicitly, are either assumed or estimated for a total power out
    of 437 MW. The inputs fixed in this function are the necessary
    inputs to obtain a square model (0 degrees of freedom).

    Unless stated otherwise, the units are: temperature in K, pressure
    in Pa, flow in mol/s, massic flow in kg/s, and heat and heat duty
    in W

    """

    ###########################################################################
    # Fix data in discharge system
    ###########################################################################
    # Add heat exchanger area from supercritical plant model_input. For
    # conceptual design optimization, area is unfixed and optimized
    m.fs.discharge.hxd.area.fix(500)

    # Define storage fluid conditions. The fluid inlet flow is fixed
    # during initialization, but is unfixed and determined during
    # optimization
    m.fs.discharge.hxd.shell_inlet.flow_mass.fix(211)
    m.fs.discharge.hxd.shell_inlet.temperature.fix(828.59)
    m.fs.discharge.hxd.shell_inlet.pressure.fix(101325)

    # Splitter inlet is fixed for initialization and will be unfixed right after
    m.fs.discharge.es_split.inlet.flow_mol.fix(19203.6)
    m.fs.discharge.es_split.inlet.enth_mol.fix(52232)
    m.fs.discharge.es_split.inlet.pressure.fix(3.4958e7)

    ###########################################################################
    # Fix data in condensate source splitter
    ###########################################################################
    # The model is built for a fixed flow of condensate through the
    # discharge heat exchanger. This condensate flow is unfixed and
    # determined during design optimization
    m.fs.discharge.es_split.split_fraction[0, "to_hxd"].fix(0.1)

    ###########################################################################
    # Fix data in storage turbine
    ###########################################################################
    m.fs.discharge.es_turbine.efficiency_isentropic.fix(0.8)


def set_scaling_factors(m):
    """Scaling factors in the flowsheet

    """

    # Include scaling factors for solar discharge heat exchanger
    for htf in [m.fs.discharge.hxd]:
        iscale.set_scaling_factor(htf.area, 1e-2)
        iscale.set_scaling_factor(
            htf.overall_heat_transfer_coefficient, 1e-3)
        iscale.set_scaling_factor(htf.tube.heat, 1e-6)
        iscale.set_scaling_factor(htf.shell.heat, 1e-6)

    for est in [m.fs.discharge.es_turbine.control_volume]:
        iscale.set_scaling_factor(est.work, 1e-6)


def initialize(m, solver=None, optarg=None, outlvl=idaeslog.NOTSET):
    """Initialize the units included in the discharge model

    """

    # Include scaling factors
    iscale.calculate_scaling_factors(m)

    # Initialize splitter
    m.fs.discharge.es_split.initialize(outlvl=outlvl,
                                       optarg=optarg)

    propagate_state(m.fs.discharge.essplit_to_hxd)
    m.fs.discharge.hxd.initialize(outlvl=outlvl,
                                  optarg=optarg)

    propagate_state(m.fs.discharge.hxd_to_esturbine)
    m.fs.discharge.es_turbine.initialize(outlvl=outlvl,
                                         optarg=optarg)
    m.fs.discharge.es_split.inlet.unfix()
    m.fs.discharge.es_turbine.control_volume.work[0].fix(-40e-6)
    m.fs.discharge.hxd.shell_inlet.temperature.fix(826.56)

    # Fix disjuncts for initialization
    m.fs.discharge.condpump_source_disjunct.indicator_var.fix(True)
    m.fs.discharge.fwh1_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh2_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh3_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh4_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh5_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.booster_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh6_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.bfp_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh8_source_disjunct.indicator_var.fix(False)
    m.fs.discharge.fwh9_source_disjunct.indicator_var.fix(False)

    # Add options to GDPopt
    m_init = m.clone()
    m_init_var_names = [v for v in m_init.component_data_objects(Var)]
    m_orig_var_names = [v for v in m.component_data_objects(Var)]

    TransformationFactory("gdp.fix_disjuncts").apply_to(m_init)

    init_results = solver.solve(m_init, options=optarg)
    print("Discharge model initialization solver termination = ",
          init_results.solver.termination_condition)

    for v1, v2 in zip(m_init_var_names, m_orig_var_names):
        v2.value == v1.value


    print(" ")
    print("***************  Discharge Model Initialized  ********************")

def build_costing(m, solver=None, optarg=None):
    """Add cost correlations for the storage design analysis

    This function is used to estimate the capital and operating cost
    of integrating a discharge storage system to the power plant and
    it contains cost correlations to estimate: (i) the capital cost of
    discharge heat exchanger and Solar salt pump, and (ii) the
    operating costs for 1 year

    """

    ###########################################################################
    # Add capital cost
    # 1. Calculate discharge heat exchanger cost
    # 2. Calculate Solar salt pump purchase cost
    # 3. Calculate total capital cost of discharge system
    
    # Main assumptions
    # 1. Salt life is assumed to outlast the plant life
    # 2. The economic objective is to minimize total annualized cost. So, cash
    # flows, discount rate, and NPV are not included in this study.
    ###########################################################################
    # Add capital cost: 1. Calculate discharge heat exchanger cost
    ###########################################################################
    # Calculate and initialize Solar salt discharge heat exchanger
    # cost, which is estimated using the IDAES costing method with
    # default options, i.e. a U-tube heat exchanger, stainless steel
    # material, and a tube length of 12ft. Refer to costing
    # documentation to change any of the default options. The purchase
    # cost of heat exchanger has to be annualized when used
    m.fs.costing = SSLWCosting()

    m.fs.discharge.hxd.costing = UnitModelCostingBlock(
        flowsheet_costing_block=m.fs.costing,
        costing_method=SSLWCostingData.cost_heat_exchanger,
    )

    m.fs.discharge.salt_purchase_cost = pyo.Expression(
        expr=(m.fs.discharge.hxd.shell_inlet.flow_mass[0] *
              m.fs.discharge.hours_per_day *
              m.fs.discharge.num_of_years *
              3600 * m.fs.discharge.solar_salt_price),
        doc="Total amount of Solar salt in kg"
    )

    ###########################################################################
    # Add capital cost: 2. Calculate Solar salt pump purchase cost
    ###########################################################################
    # Pump for moving Solar salt is not explicity modeled. To compute
    # the capital costs for this pump the capital cost expressions are
    # added below.  All cost expressions are from the same reference
    # as the IDAES costing framework and is given below: Seider,
    # Seader, Lewin, Windagdo, 3rd Ed. John Wiley and Sons, Chapter
    # 22. Cost Accounting and Capital Cost Estimation, Section 22.2 Cost
    # Indexes and Capital Investment

    # ---------- Solar salt ----------
    # Calculate purchase cost of Solar salt pump
    m.fs.discharge.spump_Qgpm = pyo.Expression(
        expr=(m.fs.discharge.hxd.
              hot_side.properties_in[0].flow_mass *
              (264.17 * pyo.units.gallon / pyo.units.m**3) *
              (60 * pyo.units.s / pyo.units.min) /
              (m.fs.discharge.hxd.
               hot_side.properties_in[0].dens_mass["Liq"])),
        doc="Conversion of Solar salt flow mass to volumetric flow in gallons/min"
    )
    m.fs.discharge.dens_lbft3 = pyo.units.convert(
        m.fs.discharge.hxd.hot_side.properties_in[0].dens_mass["Liq"],
        to_units=pyo.units.pound / pyo.units.foot**3
    )
    m.fs.discharge.spump_sf = pyo.Expression(
        expr=(m.fs.discharge.spump_Qgpm *
              (m.fs.discharge.spump_head ** 0.5)),
        doc="Pump size factor"
    )
    # Expression for pump base purchase cost
    m.fs.discharge.pump_CP = pyo.Expression(
        expr=(
            m.fs.discharge.spump_FT * m.fs.discharge.spump_FM *
            exp(
                9.7171 -
                0.6019 * log(m.fs.discharge.spump_sf) +
                0.0519 * ((log(m.fs.discharge.spump_sf))**2)
            )
        ),
        doc="Base purchase cost of Solar salt pump in $"
    )
    # Expression for pump efficiency
    m.fs.discharge.spump_np = pyo.Expression(
        expr=(
            -0.316 +
            0.24015 * log(m.fs.discharge.spump_Qgpm) -
            0.01199 * ((log(m.fs.discharge.spump_Qgpm))**2)
        ),
        doc="Fractional efficiency of the pump in horsepower"
    )
    m.fs.discharge.motor_pc = pyo.Expression(
        expr=(
            (m.fs.discharge.spump_Qgpm *
             m.fs.discharge.spump_head *
             m.fs.discharge.dens_lbft3) /
            (33000 *
             m.fs.discharge.spump_np *
             m.fs.discharge.spump_nm)
        ),
        doc="Power consumption of motor in horsepower"
    )

    # Defining a local variable for the log of motor's power consumption
    # This will help writing the motor's purchase cost expressions conciesly
    _log_motor_pc = log(m.fs.discharge.motor_pc)

    # Expression for motor's purchase cost
    m.fs.discharge.motor_CP = pyo.Expression(
        expr=(
            m.fs.discharge.spump_motorFT *
            exp(
                5.8259 +
                0.13141 * _log_motor_pc +
                0.053255 * (_log_motor_pc**2) +
                0.028628 * (_log_motor_pc**3) -
                0.0035549 * (_log_motor_pc**4)
            )
        ),
        doc="Base cost of Solar salt pump's motor in $"
    )

    # Calculate and initialize total cost of Solar salt pump
    m.fs.discharge.spump_purchase_cost = pyo.Var(
        initialize=100000,
        bounds=(0, 1e7),
        doc="Total purchase cost of Solar salt pump in $"
    )

    def solar_spump_purchase_cost_rule(b):
        return (
            m.fs.discharge.spump_purchase_cost == (
                m.fs.discharge.pump_CP +
                m.fs.discharge.motor_CP) *
            (m.CE_index / 394)
        )
    m.fs.discharge.spump_purchase_cost_eq = pyo.Constraint(
        rule=solar_spump_purchase_cost_rule)

    calculate_variable_from_constraint(
        m.fs.discharge.spump_purchase_cost,
        m.fs.discharge.spump_purchase_cost_eq)

    ###########################################################################
    # Add capital cost: 3. Calculate total capital cost for discharge system
    ###########################################################################

    # Add capital cost variable at flowsheet level to handle the Solar
    # salt capital cost
    m.fs.discharge.capital_cost = pyo.Var(
        initialize=1000000,
        bounds=(0, 1e10),
        doc="Annualized capital cost in $/year")

    # Calculate and initialize annualized capital cost for the Solar
    # salt discharge storage system
    def solar_cap_cost_rule(b):
        return (
            m.fs.discharge.capital_cost *
            m.fs.discharge.num_of_years
        ) == (m.fs.discharge.spump_purchase_cost +
               m.fs.discharge.salt_purchase_cost +
              m.fs.discharge.hxd.costing.capital_cost)
    m.fs.discharge.cap_cost_eq = pyo.Constraint(
        rule=solar_cap_cost_rule)

    calculate_variable_from_constraint(
        m.fs.discharge.capital_cost,
        m.fs.discharge.cap_cost_eq)

    ###########################################################################
    #  Add operating cost
    ###########################################################################
    m.fs.discharge.operating_hours = pyo.Expression(
        expr=365 * 3600 * m.fs.discharge.hours_per_day,
        doc="Number of operating hours per year")
    m.fs.discharge.operating_cost = pyo.Var(
        initialize=1000000,
        bounds=(0, 1e11),
        doc="Operating cost in $/year")

    def op_cost_rule(b):
        return m.fs.discharge.operating_cost == (
            m.fs.discharge.operating_hours *
            m.fs.discharge.coal_price *
            m.fs.coal_heat_duty * 1e6
        )
    m.fs.discharge.op_cost_eq = pyo.Constraint(rule=op_cost_rule)

    # Initialize operating cost
    calculate_variable_from_constraint(
        m.fs.discharge.operating_cost,
        m.fs.discharge.op_cost_eq)

    ###########################################################################
    #  Add capital and operating cost for full plant
    ###########################################################################

    # Add variables and functions to calculate the plant capital cost
    # and plant variable and fixed operating costs.
    m.fs.discharge.plant_capital_cost = pyo.Var(
        initialize=1000000,
        bounds=(0, 1e12),
        doc="Annualized capital cost for the plant in $")
    m.fs.discharge.plant_fixed_operating_cost = pyo.Var(
        initialize=1000000,
        bounds=(0, 1e12),
        doc="Plant fixed operating cost in $/yr")
    m.fs.discharge.plant_variable_operating_cost = pyo.Var(
        initialize=1000000,
        bounds=(0, 1e12),
        doc="Plant variable operating cost in $/yr")

    def plant_cap_cost_rule(b):
        return b.plant_capital_cost == (
            ((2688973 * m.fs.plant_power_out[0]  # in MW
              + 618968072) /
             b.num_of_years
            ) * (m.CE_index / 575.4)
        )
    m.fs.discharge.plant_cap_cost_eq = Constraint(rule=plant_cap_cost_rule)

    # Initialize capital cost of power plant
    calculate_variable_from_constraint(
        m.fs.discharge.plant_capital_cost,
        m.fs.discharge.plant_cap_cost_eq)

    def op_fixed_plant_cost_rule(b):
        return b.plant_fixed_operating_cost == (
            ((16657.5 * m.fs.plant_power_out[0]  # in MW
              + 6109833.3) /
             b.num_of_years
            ) * (m.CE_index / 575.4)  # annualized, in $/y
        )
    m.fs.discharge.op_fixed_plant_cost_eq = pyo.Constraint(
        rule=op_fixed_plant_cost_rule)

    def op_variable_plant_cost_rule(b):
        return b.plant_variable_operating_cost == (
            (31754.7 * m.fs.plant_power_out[0]  # in MW
            ) * (m.CE_index / 575.4) # in $/yr
        )
    m.fs.discharge.op_variable_plant_cost_eq = pyo.Constraint(
        rule=op_variable_plant_cost_rule)

    # Initialize plant fixed and variable operating costs
    calculate_variable_from_constraint(
        m.fs.discharge.plant_fixed_operating_cost,
        m.fs.discharge.op_fixed_plant_cost_eq)
    calculate_variable_from_constraint(
        m.fs.discharge.plant_variable_operating_cost,
        m.fs.discharge.op_variable_plant_cost_eq)

    # Clone the model to transform and initialize
    # then copy the initialized variable values
    m_cost = m.clone()
    m_cost_var_names = [v for v in m_cost.component_data_objects(Var)]
    m_orig_var_names = [v for v in m.component_data_objects(Var)]

    TransformationFactory("gdp.fix_disjuncts").apply_to(m_cost)

    # Check and raise an error if the degrees of freedom are not 0
    if not degrees_of_freedom(m_cost) == 0:
        raise ConfigurationError(
            "The degrees of freedom after building the model are not 0. "
            "You have {} degrees of freedom. "
            "Please check your inputs to ensure a square problem "
            "before initializing the model.".format(degrees_of_freedom(m_cost))
            )

    cost_results = solver.solve(m_cost, options=optarg)
    print("Discharge model initialization solver termination = ",
          cost_results.solver.termination_condition)

    for v1, v2 in zip(m_cost_var_names, m_orig_var_names):
        v2.value == v1.value

    print("***************  Discharge Costing Initialized  ******************")


def unfix_disjuncts_post_initialization(m):
    """This method unfixes the disjuncts that were fixed only
    for initializing the model.

    """

    m.fs.discharge.condpump_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh1_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh2_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh3_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh4_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh5_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh6_source_disjunct.indicator_var.unfix()
    m.fs.discharge.booster_source_disjunct.indicator_var.unfix()
    m.fs.discharge.bfp_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh8_source_disjunct.indicator_var.unfix()
    m.fs.discharge.fwh9_source_disjunct.indicator_var.unfix()

    print("******************** Disjuncts Unfixed *************************")
    print()
    print()


def add_bounds(m, power_max=None):
    """Add bounds to all units in discharge model

    """

    m.flow_max = m.main_flow * 3        # Units in mol/s
    m.storage_flow_max = 0.2 * m.flow_max # Units in mol/s
    m.salt_flow_max = 1000                # Units in kg/s
    m.heat_duty_bound = 200e6             # Units in MW
    m.power_max = power_max               # Units in MW

    # Add bounds to Solar salt discharge heat exchanger
    for hxd in [m.fs.discharge.hxd]:
        hxd.tube_inlet.flow_mol.setlb(0)
        hxd.tube_inlet.flow_mol.setub(m.storage_flow_max)
        hxd.shell_inlet.flow_mass.setlb(0)
        hxd.shell_inlet.flow_mass.setub(m.salt_flow_max)
        hxd.tube_outlet.flow_mol.setlb(0)
        hxd.tube_outlet.flow_mol.setub(m.storage_flow_max)
        hxd.shell_outlet.flow_mass.setlb(0)
        hxd.shell_outlet.flow_mass.setub(m.salt_flow_max)
        hxd.shell_inlet.pressure.setlb(101320)
        hxd.shell_inlet.pressure.setub(101330)
        hxd.shell_outlet.pressure.setlb(101320)
        hxd.shell_outlet.pressure.setub(101330)
        hxd.heat_duty.setlb(0)
        hxd.heat_duty.setub(m.heat_duty_bound)
        hxd.shell.heat.setlb(-m.heat_duty_bound)
        hxd.shell.heat.setub(0)
        hxd.tube.heat.setlb(0)
        hxd.tube.heat.setub(m.heat_duty_bound)
        hxd.shell.properties_in[0].enth_mass.setlb(0)
        hxd.shell.properties_in[0].enth_mass.setub(1.5e6)
        hxd.shell.properties_out[0].enth_mass.setlb(0)
        hxd.shell.properties_out[0].enth_mass.setub(1.5e6)
        hxd.overall_heat_transfer_coefficient.setlb(0)
        hxd.overall_heat_transfer_coefficient.setub(10000)
        hxd.area.setlb(0)
        hxd.area.setub(5000)
        hxd.costing.pressure_factor.setlb(0)
        hxd.costing.pressure_factor.setub(1e5)
        hxd.costing.capital_cost.setlb(0)
        hxd.costing.capital_cost.setub(1e7)
        hxd.costing.base_cost_per_unit.setlb(0)
        hxd.costing.base_cost_per_unit.setub(1e6)
        hxd.costing.material_factor.setlb(0)
        hxd.costing.material_factor.setub(10)
        hxd.delta_temperature_in.setlb(9)
        hxd.delta_temperature_out.setlb(5)
        hxd.delta_temperature_in.setub(350)
        hxd.delta_temperature_out.setub(350)

    # Add bounds needed in units declared in condensate source
    # disjunction
    for split in [m.fs.discharge.es_split]:
        split.inlet.flow_mol[:].setlb(0)
        split.inlet.flow_mol[:].setub(m.flow_max)
        split.to_hxd.flow_mol[:].setlb(0)
        split.to_hxd.flow_mol[:].setub(m.storage_flow_max)
        split.to_fwh.flow_mol[:].setlb(0)
        split.to_fwh.flow_mol[:].setub(m.flow_max)
        split.split_fraction[0.0, "to_hxd"].setlb(0)
        split.split_fraction[0.0, "to_hxd"].setub(1)
        split.split_fraction[0.0, "to_fwh"].setlb(0)
        split.split_fraction[0.0, "to_fwh"].setub(1)

    m.fs.plant_power_out[0].setlb(283)
    m.fs.plant_power_out[0].setub(m.power_max)

    m.fs.deaerator.steam.flow_mol[:].setlb(0)
    m.fs.deaerator.steam.flow_mol[:].setub(m.flow_max)
    m.fs.deaerator.drain.flow_mol[:].setlb(0)
    m.fs.deaerator.drain.flow_mol[:].setub(m.flow_max)
    m.fs.deaerator.mixed_state[:].flow_mol.setlb(0)
    m.fs.deaerator.mixed_state[:].flow_mol.setub(m.flow_max)
    m.fs.deaerator.feedwater.flow_mol[:].setlb(0)
    m.fs.deaerator.feedwater.flow_mol[:].setub(m.flow_max)

    for unit_k in [m.fs.booster]:
        unit_k.inlet.flow_mol[:].setlb(0)
        unit_k.inlet.flow_mol[:].setub(m.flow_max)
        unit_k.outlet.flow_mol[:].setlb(0)
        unit_k.outlet.flow_mol[:].setub(m.flow_max)

    for k in m.set_turbine:
        m.fs.turbine[k].work.setlb(-1e12)
        m.fs.turbine[k].work.setub(0)


def main(m_usc, solver=None, optarg=None):

    # Add boiler and cycle efficiencies to the model
    add_efficiency = True

    # Add maximum power produced by power plant in MW. For this
    # analysis, the maximum power is fixed to 436 MW
    power_max = 436

    # Create a flowsheet, add properties, unit models, and arcs
    m = create_discharge_model(m_usc,
                               add_efficiency=add_efficiency,
                               power_max=power_max)

    # Give all the required inputs to the model
    set_model_input(m)

    # Add disjunction
    add_disjunction(m)

    # Add scaling factor
    set_scaling_factors(m)

    # Initialize the model with a sequential initialization
    initialize(m, solver=solver, optarg=optarg)

    # Add cost correlations
    build_costing(m, solver=solver, optarg=optarg)

    # Unfix disjuncts
    unfix_disjuncts_post_initialization(m)

    # Add bounds
    add_bounds(m, power_max=power_max)

    return m


def print_model(solver_obj, nlp_model, nlp_data, csvfile):
    """Print the disjunction selected during the solution of the NLP
    subproblem

    """

    m_iter = solver_obj.iteration
    nlp_model.disjunction1_selection = {}
    nlp_model.objective_value = {}
    nlp_model.area = {}
    nlp_model.ohtc = {}
    nlp_model.hot_salt_temp = {}
    nlp_model.cold_salt_temp = {}
    nlp_model.hot_steam_temp = {}
    nlp_model.cold_steam_temp = {}
    nlp_model.turbine_inlet_temp = {}
    nlp_model.turbine_inlet_temp_sat = {}
    nlp_model.storage_material_amount = {}
    nlp_model.storage_material_flow = {}
    nlp_model.steam_flow_to_storage = {}
    nlp_model.plant_power = {}
    nlp_model.boiler_eff = {}
    nlp_model.cycle_eff = {}

    nlp = nlp_model.fs.discharge

    print('    ___________________________________________')
    print('     Disjunction 1:')
    if nlp.condpump_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'Condenser Pump'
        print('      Condensate from condenser pump is selected')
    elif nlp.booster_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'Booster Pump'
        print('      Condensate from booster pump is selected')
    elif nlp.bfp_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'BFP'
        print('      Condensate from boiler feed pump is selected')
    elif nlp.fwh9_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH9'
        print('      Condensate from FWH9 is selected')
    elif nlp.fwh1_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH1'
        print('      Condensate from FWH1 is selected')
    elif nlp.fwh2_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH2'
        print('      Condensate from FWH2 is selected')
    elif nlp.fwh3_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH3'
        print('      Condensate from FWH3 is selected')
    elif nlp.fwh6_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH6'
        print('      Condensate from FWH6 is selected')
    elif nlp.fwh8_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH8'
        print('      Condensate from FWH8 is selected')
    elif nlp.fwh4_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH4'
        print('      Condensate from FWH4 is selected')
    elif nlp.fwh5_source_disjunct.binary_indicator_var.value == 1:
        nlp_model.disjunction1_selection[m_iter] = 'FWH5'
        print('        Disjunction 1: Condensate from FWH5 is selected')
    else:
        print('      Error: There are no more alternatives')
    print('    ___________________________________________')
    print()
    nlp_model.objective_value[m_iter] = pyo.value(nlp_model.obj) / scaling_obj
    nlp_model.storage_material_amount[m_iter] = pyo.value(nlp_model.fs.discharge.salt_purchase_cost) / (3600 * 0.49)
    nlp_model.area[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.area)
    nlp_model.ohtc[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.overall_heat_transfer_coefficient[0])
    nlp_model.hot_salt_temp[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.shell_inlet.temperature[0])
    nlp_model.cold_salt_temp[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.shell_outlet.temperature[0])
    nlp_model.cold_steam_temp[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.cold_side.properties_in[0].temperature)
    nlp_model.hot_steam_temp[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.cold_side.properties_out[0].temperature)
    nlp_model.turbine_inlet_temp[m_iter] = pyo.value(nlp_model.fs.discharge.es_turbine.control_volume.properties_in[0].temperature)
    nlp_model.turbine_inlet_temp_sat[m_iter] = pyo.value(nlp_model.fs.discharge.es_turbine.control_volume.properties_in[0].temperature_sat)
    nlp_model.storage_material_flow[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.shell_outlet.flow_mass[0])
    nlp_model.steam_flow_to_storage[m_iter] = pyo.value(nlp_model.fs.discharge.hxd.tube_outlet.flow_mol[0])
    nlp_model.plant_power[m_iter] = pyo.value(nlp_model.fs.plant_power_out[0])
    nlp_model.boiler_eff[m_iter] = pyo.value(nlp_model.fs.boiler_efficiency) * 100
    nlp_model.cycle_eff[m_iter] = pyo.value(nlp_model.fs.cycle_efficiency) * 100

    if True:
        writer = csv.writer(csvfile)
        writer.writerow(
            (
                m_iter,
                nlp_model.disjunction1_selection[m_iter],
                nlp_model.objective_value[m_iter],
                nlp_model.storage_material_amount[m_iter],
                nlp_model.area[m_iter],
                nlp_model.ohtc[m_iter],
                nlp_model.hot_salt_temp[m_iter],
                nlp_model.cold_salt_temp[m_iter],
                nlp_model.cold_steam_temp[m_iter],
                nlp_model.hot_steam_temp[m_iter],
                nlp_model.turbine_inlet_temp[m_iter],
                nlp_model.turbine_inlet_temp_sat[m_iter],
                nlp_model.storage_material_flow[m_iter],
                nlp_model.steam_flow_to_storage[m_iter],
                nlp_model.plant_power[m_iter],
                nlp_model.boiler_eff[m_iter],
                nlp_model.cycle_eff[m_iter]
            ),
        )
        csvfile.flush()


def create_csv_header():
    csvfile = open('subnlp_master_iterations_discharge_superstructure_results.csv',
                   'w', newline='')
    writer = csv.writer(csvfile)
    writer.writerow(
        ('Iteration', 'Disjunction 1 (Source selection)', 'Obj ($/h)',
         'StorageMaterialAmount(metric_ton)', 'HXArea(m2)', 'Overall HTC(W/m2/K)',
         'HotSaltTemp(K)', 'ColdSaltTemp(K)', 'ColdSsteamTemp(K)', 'HotSteamTemp(K)',
          'TurbineInletTemp (K)', 'TurbineInletTempSat (K)', 'Hxd Salt Flow (kg/s)', 'Hxd Steam Flow (mol/s)', 
          'Plant Power (MW)', 'BoilerEff(%)', 'CycleEff(%)')
    )
    return csvfile


def run_gdp(m):
    """Declare solver GDPopt and its options
    """

    csvfile = create_csv_header()

    # Add options to GDPopt
    opt = SolverFactory('gdpopt')
    _prop_bnds_root_to_leaf_map[ExternalFunctionExpression] = lambda x, y, z: None

    # Solve model
    results = opt.solve(
        m,
        tee=True,
        algorithm='RIC',
        init_algorithm="no_init",
        mip_solver='gurobi',
        nlp_solver='ipopt',
        call_after_subproblem_solve=(lambda c, a, b: print_model(c, a, b, csvfile)),
        subproblem_presolve = False,
        nlp_solver_args=dict(
            tee=True,
            symbolic_solver_labels=True,
            options={
                "linear_solver": "ma57",
                "max_iter": 200
            }
        )
    )
    csvfile.close()
    return results


def print_results(m, results):

    print('====================================================')
    print('Results ')
    print()
    print('Obj (M$/year): {:.2f}'.format(
        (pyo.value(m.obj) / scaling_obj) * 1e-6))
    print()
    print("**Discrete design decisions (Disjunction)")
    for d in m.component_data_objects(ctype=Disjunct,
                                      active=True,
                                      sort=True, descend_into=True):
        if abs(d.binary_indicator_var.value - 1) < 1e-6:
            print(d.name, ' should be selected!')
    print('Discharge heat exchanger area (m2): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.area)))
    print('steam flow (mol/s): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.tube_outlet.flow_mol[0])))
    print('salt flow (mol/s): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.shell_outlet.flow_mass[0])))
    print('steam temp in (K): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.cold_side.properties_in[0].temperature)))
    print('steam temp out (K): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.cold_side.properties_out[0].temperature)))
    print('salt temp in (K): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.shell_inlet.temperature[0])))
    print('salt temp out (K): {:.2f}'.format(
        pyo.value(m.fs.discharge.hxd.shell_outlet.temperature[0])))
    print('====================================================')
    print()
    print('Solver details')
    print(results)
    print()


def model_analysis(m, heat_duty=None):
    """Solve the conceptual design optimization problem

    """

    # Fix variables in the flowsheet
    m.fs.boiler.outlet.pressure.fix(m.main_steam_pressure)
    m.fs.discharge.hxd.heat_duty.fix(heat_duty * 1e6)

    # Unfix variables that were fixed iduring initialization
    m.fs.boiler.inlet.flow_mol.unfix()
    m.fs.discharge.es_split.split_fraction[0, "to_hxd"].unfix()
    m.fs.discharge.es_split.inlet.unfix()
    m.fs.discharge.hxd.shell_inlet.flow_mass.unfix()
    m.fs.discharge.hxd.area.unfix()

    m.fs.net_power_cons = Constraint(
        expr=(m.fs.plant_power_out[0] == 436),
        doc="Fixing the net power"
    )

    # Calculate revenue
    m.fs.revenue = Expression(
        expr=(40 * m.fs.net_power),
        doc="Revenue function in $/h assuming 1 hr operation"
    )

    m.obj = Objective(
        expr=(
            m.fs.revenue -
            (m.fs.discharge.operating_cost +
             m.fs.discharge.plant_fixed_operating_cost +
             m.fs.discharge.plant_variable_operating_cost) / (365 * 24) -
            (m.fs.discharge.capital_cost +
            m.fs.discharge.operating_cost) / (365 * 24)
            ) * scaling_obj,
            sense=maximize
    )


if __name__ == "__main__":

    optarg = {
        "max_iter": 300,
        "halt_on_ampl_error": "yes"
        }
    solver = get_solver('ipopt', optarg)

    # Build ultra-supercritical plant base model
    m_usc = usc.build_plant_model()

    # Initialize ultra-supercritical plant base model
    usc.initialize(m_usc)

    # Build discharge model
    m = main(m_usc, solver=solver, optarg=optarg)

    # Solve design model optimization problem
    heat_duty_data = 148.5
    model_analysis(m, heat_duty=heat_duty_data)

    # Solve model using GDPopt
    print()
    print('**********Start solution of GDP discharge model using GDPopt')
    print('DOFs before GDP discharge model solution: ', degrees_of_freedom(m))
    print()
    results = run_gdp(m)

    print_results(m, results)

