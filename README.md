# QRes
QGIS plugin for mapping urban resilience
![alt text](https://github.com/seelca/Resilience_mapper/blob/main/resmap.JPG)

QRES is a QGIS plugin designed to assess the resilience of urban areas by analysing the spatial accessibility of essential features such as schools, parks, hospitals, and public transport.
Unlike traditional distance-based methods, QRES uses isochrones—areas reachable within equal travel times—to calculate resilience values that better reflect real-world conditions.

The plugin’s workflow consists of three automated steps:\n
1. Extract relevant urban features and generate isochrones using Mapbox;
2. Compute resilience values for each sampled point, considering feature redundancy and accessibility;
3. Produce attribute table outputs for further GIS (heatmaps) or CSV analysis.


The method was first introduced in:

Pintacuda, L., Carta, S., Turchi, T., Sabiu, M. (2023) QRES: A QGIS plugin to calculate resilience based on the proximity of urban resources, in Responsive Cities: Collective Intelligence Design Symposium Proceedings, IAAC, Barcelona.

This approach enables planners and researchers to simulate and compare urban performance, fostering adaptive, climate-resilient design strategies.

⚙️ Tips for Use and Performance:
Calculating large sets of points may take some time. A progress bar is displayed on screen to monitor the process.\n
If the plugin becomes unresponsive and needs to be relaunched, there is no need to start over — points that were already processed will retain their resilience values. You can simply copy the missing points into a new layer and calculate only the remaining subset.

🌐 Mapbox Requirement:
The plugin requires you to sign up for a Mapbox account to generate isochrones. Although Mapbox is a paid service, its free usage tier is generous and allows you to analyse large urban areas without cost in most scenarios.

We appreciate your interest and feedback on this tool. Every comment helps us improve its performance. Development of QRES v2 is already underway, focusing on enhanced speed, stability, and overall reliability.
