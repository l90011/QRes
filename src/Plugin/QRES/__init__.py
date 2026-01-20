# -*- coding: utf-8 -*-
"""
/***************************************************************************
 ResilienceMapper
                                 A QGIS plugin
 Calculates and maps urban resilience values based on isochrone analysis.
                             -------------------
        begin                : 2023-04-28
        version              : 1.0.0
        qgisMinimumVersion   : 3.0
        author               : MKS DTECH, Luigi Pintacuda, Silvio Carta, Tommaso Turchi
        email                : l.pintacuda@herts.ac.uk
        category             : Analysis
        tags                 : urban, resilience, analysis, isochrones, planning
        experimental         : false
        deprecated           : false
        server               : false
 ***************************************************************************/

 About:
 QRES is a QGIS plugin designed to assess the resilience of urban areas by analysing
 the spatial accessibility of essential features such as schools, parks, hospitals,
 and public transport.

 Unlike traditional distance-based methods, QRES uses isochrones to calculate
 resilience values that better reflect real-world conditions.

 Workflow:
 1. Generate isochrones
 2. Compute resilience values
 3. Produce GIS and CSV outputs

***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""
# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load plugin class.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .resilient_iso import ResilientIsochrones
    return ResilientIsochrones(iface)
