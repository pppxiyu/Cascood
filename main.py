import utils.preprocess_station as pp_s
import utils.preprocess_roads as pp_r
import utils.preprocess_incidents as pp_i
import utils.preprocess_graph as pp_g
import model as mo
import utils.visualization as vis
import utils.regression as reg
from config_vb import *
import geopandas as gpd
import os


def pull_road_data_osm():
    # pull road data
    pp_r.pull_roads_osm(
        'Virginia Beach, VA, USA', 'vb',
        './data/roads',
        '"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service"',
    )


def pull_geocode_incident():
    # geocode incidents data ans save it
    pp_i.geocode_incident(
        './data/incidents/virginiaBeach_ambulance_timeData.csv',
        ['2013-01-01', '2013-01-02'],
        './data/incidents/geocoded/20130101-20130102.geojson',
    )


def save_inundated_roads():
    # overlapping the inundation and roads and save it
    road_segment = gpd.read_file(dir_road)
    for i in range(25, 48 + 1):
        road_segment = pp_r.add_water_depth_on_roads_w_bridge(
            road_segment,
            f'./data/inundation/tif_data/depth_objID_{i}.tif', i,
            {'max': 'max_depth', 'mean': 'mean_depth'}
        )
    road_segment.to_file(dir_road_inundated, driver='GeoJSON')


def save_inundated_roads_4_sim():
    import os
    if not os.path.exists(dir_road_cube6_inundated):
        road_segment = gpd.read_file(dir_road)
        road_shp_sim = gpd.read_file(dir_road_cube6)
        for i in range(25, 48 + 1):
            road_shp_sim = pp_r.add_water_depth_on_roads_w_bridge(
                road_shp_sim,
                f'./data/inundation/tif_data/depth_objID_{i}.tif', i,
                {'max': 'max_depth', 'mean': 'mean_depth'},
                remove_bridge=False,
                remove_inundation_under_bridge=True,
                geo_w_bridge=road_segment,
            )
        road_shp_sim.to_file(dir_road_cube6_inundated, driver='GeoJSON')


def save_rescue_data():
    # process rescue station data and save it
    road_intersection = gpd.read_file(f"data/bch/roads/road_intersection_vb.geojson")
    road_segment = gpd.read_file(f"data/bch/roads/road_segment_vb.geojson")
    rescue_station = pp_s.import_rescue_station('./data/rescue_team_location/rescue_stations.txt')
    rescue_station['nearest_segment'] = rescue_station.to_crs(crs_prj).geometry.apply(
        lambda x: pp_s.add_nearest_segment(x, road_segment.to_crs(crs_prj))
    )
    rescue_station['nearest_intersection'] = rescue_station.to_crs(crs_prj).geometry.apply(
        lambda x: pp_s.add_nearest_intersection(x, road_intersection.to_crs(crs_prj))
    )
    rescue_station.to_csv(dir_rescue_station_n_nearest_geo)
    return


def match_osm_n_vdot():
    pp_r.match_osm_n_VDOT(
        dir_road_inundated, dir_road_cube6, dir_match_osm_n_VDOT,
        dir_VDOT_info=f'{dir_road_cube6_out_c}/AM_PK_FDBKNET_LINK.dbf',
    )


def build_full_graph_arcgis():
    import arcpy

    road_segment = pp_r.import_road_seg_w_inundation_info(dir_road_inundated, speed_assigned)
    turn_restriction = pp_r.import_turn_restriction(dir_turn)

    max_edge_turn_fd = 8

    wrong_restriction_osm_list = [
        8281368, 8898632, 10393138, 10393444, 10393445,
        10393446, 11367492, 11367493, 11735305, 11735306,
        14109533
    ]
    wrong_restriction_osmnx_list = [  # eliminating is technically wrong, but does not affect results
        10064810, 14185206, 14594122, 17505468
    ]
    fields = [  # do not change the head, the sequence of elements are used below
        'SHAPE@',
        'Edge1End',
        'Edge1FCID', 'Edge1FID', 'Edge1Pos',
        'Edge2FCID', 'Edge2FID', 'Edge2Pos',
        'Edge3FCID', 'Edge3FID', 'Edge3Pos',
        'Edge4FCID', 'Edge4FID', 'Edge4Pos',
        'Edge5FCID', 'Edge5FID', 'Edge5Pos',
        'Edge6FCID', 'Edge6FID', 'Edge6Pos',
        'Edge7FCID', 'Edge7FID', 'Edge7Pos',
        'Edge8FCID', 'Edge8FID', 'Edge8Pos',
    ]
    assert ((len(fields) - 2) / 3) == max_edge_turn_fd, 'Field length is wrong.'

    # create network dataset
    if arcpy.Exists(f'{geodatabase_addr}/{fd_name}/{nd_name}') is not True:
        pp_g.build_feature_dataset_arcgis(geodatabase_addr, road_segment)
        pp_g.build_network_dataset_arcgis(geodatabase_addr)
    desc = arcpy.Describe(f'{geodatabase_addr}/{fd_name}/{nd_name}')
    if hasattr(desc, 'turnFeatureClasses') is not True:
        arcpy.na.CreateTurnFeatureClass(
            f'{geodatabase_addr}/{fd_name}',
            turn_name, 8,
        )
        pp_g.add_turn_restriction_arcgis(
            f'{geodatabase_addr}/{fd_name}',
            fields,
            turn_name,
            turn_restriction, road_segment,
            wrong_restriction_osm_list + wrong_restriction_osmnx_list,
        )

    # manual input
    input(
        """
            Must conduct following operation in network dataset properties manually:
            1) Create travel mode, name it DriveTime
            2) Define travel cost attribute 'travel_time_s', unit is seconds. 
                Evaluator is field script !travel_time_s!.
            3) Define one-way restriction. Prohibit all against direction.
            4) Define turn restrictions. Add it in source. Apply all turn restrictions.
            5) Define inundation restriction. Keep it as default.
            6) Set costs and restrictions in the travel mode.
            7) Build network.
            Press Enter to continue.
        """
    )
    arcpy.BuildNetwork_na(f'{geodatabase_addr}/{fd_name}/{nd_name}')

    return


def calculate_all_routes(op):
    rescue_sta = pp_s.import_rescue_station(dir_rescue_station_n_nearest_geo)
    incidents = pp_i.import_incidents_add_info(
        dir_incidents, rescue_sta, period_dict, routing_nearest=dir_incidents_routing_nearest,
    )

    if op == 1:
        # OP1: nearest origin
        road_segment = pp_r.import_road_seg_w_inundation_info(dir_road_inundated, speed_assigned)
        route_analysis = mo.RouteAnalysis(incidents, 'Number_nearest')
        route_analysis.run_route_analysis_arcgis(
            geodatabase_addr, fd_name, nd_name, nd_layer_name,
            rescue_sta, road_segment,
        )

    elif op == 2:
        # OP2: real origin
        road_segment = pp_r.import_road_seg_w_inundation_info(dir_road_inundated, speed_assigned)
        route_analysis = mo.RouteAnalysis(incidents, 'Rescue Squad Number', mode_label='_o')
        route_analysis.run_route_analysis_arcgis(
            geodatabase_addr, fd_name, nd_name, nd_layer_name,
            rescue_sta, road_segment,
        )

    elif op == 3:
        # OP3: everyday congestion
        road_segment_VDOT = {
            '9-13': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_c}/AM_PK_FDBKNET_LINK.dbf'
            ),
            '13-19': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_c}/Md_OP_FDBKNET_LINK.dbf'
            ),
            '19-22': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_c}/PM_PK_FDBKNET_LINK.dbf'
            ),
            '22-9': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_c}/Nt_OP_FDBKNET_LINK.dbf'
            ),
        }  # NOTE: UTC TIME !!! 2016-10-09 was in EDT.  Local time  + 4 hours = UTC time.
        road_segment = pp_r.import_road_seg_w_inundation_info(
            dir_road_inundated, speed_assigned,
            VDOT_speed=road_segment_VDOT,
            VDOT_speed_col = 'CSPD_1',
            osm_match_vdot=dir_match_osm_n_VDOT,
        )
        route_analysis = mo.RouteAnalysis(incidents, 'Number_nearest', mode_label='_daily_c')
        route_analysis.run_route_analysis_arcgis(
            geodatabase_addr, fd_name, nd_name, nd_layer_name,
            rescue_sta, road_segment, if_do_normal=False,
        )

    elif op == 4:
        # OP4: flooding congestion
        road_segment_VDOT = {
            '9-13': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_AM_PK/AM_PK_FDBKNET_LINK.dbf'
            ),
            '13-19': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_Md_OP/Md_OP_FDBKNET_LINK.dbf'
            ),
            '19-22': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_PM_PK/PM_PK_FDBKNET_LINK.dbf'
            ),
            '22-9': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_Nt_OP/Nt_OP_FDBKNET_LINK.dbf'
            ),
        }  # NOTE: UTC TIME !!! 2016-10-09 was in EDT.  Local time  + 4 hours = UTC time.
        road_segment = pp_r.import_road_seg_w_inundation_info(
            dir_road_inundated, speed_assigned,
            VDOT_speed=road_segment_VDOT,
            VDOT_speed_col='TIME_1',
            osm_match_vdot=dir_match_osm_n_VDOT,
        )
        route_analysis = mo.RouteAnalysis(incidents, 'Number_nearest', mode_label='_flood_c')
        route_analysis.run_route_analysis_arcgis(
            geodatabase_addr, fd_name, nd_name, nd_layer_name,
            rescue_sta, road_segment, if_do_normal=False,
        )

    elif op == 5:
        # OP5: congestion and origin shift
        road_segment_VDOT = {
            '9-13': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_AM_PK/AM_PK_FDBKNET_LINK.dbf'
            ),
            '13-19': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_Md_OP/Md_OP_FDBKNET_LINK.dbf'
            ),
            '19-22': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_PM_PK/PM_PK_FDBKNET_LINK.dbf'
            ),
            '22-9': pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_d}_Nt_OP/Nt_OP_FDBKNET_LINK.dbf'
            ),
        }  # NOTE: UTC TIME !!! 2016-10-09 was in EDT.  Local time  + 4 hours = UTC time.
        road_segment = pp_r.import_road_seg_w_inundation_info(
            dir_road_inundated, speed_assigned,
            VDOT_speed=road_segment_VDOT,
            VDOT_speed_col='TIME_1',
            osm_match_vdot=dir_match_osm_n_VDOT,
        )
        route_analysis = mo.RouteAnalysis(incidents, 'Rescue Squad Number', mode_label='_all')
        route_analysis.run_route_analysis_arcgis(
            geodatabase_addr, fd_name, nd_name, nd_layer_name,
            rescue_sta, road_segment, if_do_normal=False,
        )


def calculate_incidents_with_gis_travel_time(op):
    incidents = pp_i.import_incident(dir_incidents)
    assert len(list(period_dict.values())) == 2

    if op == 1:
        # OP1: nearest origin
        incidents = pp_i.convert_feature_class_to_df(
            incidents,
            f'{geodatabase_addr}/route_results',
            ['normal'] + list(range(list(period_dict.values())[0], list(period_dict.values())[1] + 1)),
            mode_label='',
        )
        incidents = pp_i.add_inaccessible_routes(
            incidents, f'{dir_inaccessible_routes}.json'
        )

    if op == 2:
        # OP2: real origin
        incidents = pp_i.convert_feature_class_to_df(
            incidents,
            f'{geodatabase_addr}/route_results',
            ['normal'] + list(range(list(period_dict.values())[0], list(period_dict.values())[1] + 1)),
            mode_label='_o',
        )
        incidents = pp_i.add_inaccessible_routes(
            incidents, f'{dir_inaccessible_routes}_o.json'
        )

    if op == 3:
        # OP3: daily congestion
        incidents = pp_i.convert_feature_class_to_df(
            incidents,
            f'{geodatabase_addr}/route_results',
            list(range(list(period_dict.values())[0], list(period_dict.values())[1] + 1)),
            mode_label='_daily_c',
        )
        incidents = pp_i.add_inaccessible_routes(
            incidents, f'{dir_inaccessible_routes}_daily_c.json'
        )

    if op == 4:
        # OP4: flood congestion
        incidents = pp_i.convert_feature_class_to_df(
            incidents,
            f'{geodatabase_addr}/route_results',
            list(range(list(period_dict.values())[0], list(period_dict.values())[1] + 1)),
            mode_label='_flood_c',
        )
        incidents = pp_i.add_inaccessible_routes(
            incidents, f'{dir_inaccessible_routes}_flood_c.json'
        )

    if op == 5:
        # OP5:
        incidents = pp_i.convert_feature_class_to_df(
            incidents,
            f'{geodatabase_addr}/route_results',
            list(range(list(period_dict.values())[0], list(period_dict.values())[1] + 1)),
            mode_label='_all',
        )
        incidents = pp_i.add_inaccessible_routes(
            incidents, f'{dir_inaccessible_routes}_all.json'
        )

    return incidents


def calculate_incidents_metrics(inc):
    # clean
    incidents_c = inc.copy()
    incidents_c = pp_i.add_period_label(incidents_c, 'Call Date and Time', period_dict)
    incidents_c = pp_i.convert_timedelta_2_seconds(incidents_c, ['TravelTime', 'ResponseTime'])
    incidents_c = incidents_c[~incidents_c['Total_Seconds'].isna()]
    incidents_c = incidents_c[~incidents_c['TravelTime'].isna()]
    incidents_c = incidents_c[(incidents_c['TravelTime'] < 0) | (incidents_c['TravelTime'] >= 60)]

    # deal with infinity
    non_inf_over = incidents_c[
        (incidents_c['period_label'] != '') &
        (incidents_c['Total_Seconds'] > incidents_c['TravelTime'])
        ]
    non_inf_over.loc[:, ['over_rate']] = non_inf_over['Total_Seconds'] / non_inf_over['TravelTime']
    incidents_c.loc[incidents_c['Total_Seconds'] == -999, ['Total_Seconds']] = incidents_c.loc[
                                                                                   incidents_c[
                                                                                       'Total_Seconds'] == -999, 'TravelTime'] * \
                                                                               non_inf_over['over_rate'].median()

    # numerical diff
    incidents_c['diff_travel'] = incidents_c['Total_Seconds'] - incidents_c['TravelTime']
    incidents_c = incidents_c[~incidents_c['diff_travel'].isna()]

    # categorical wellness
    service_area_threshold = 300
    incidents_c['false_positive'] = ((incidents_c['Total_Seconds'] <= service_area_threshold)
                                     & (incidents_c['TravelTime'] > service_area_threshold))
    incidents_c['false_negative'] = ((incidents_c['Total_Seconds'] > service_area_threshold)
                                     & (incidents_c['TravelTime'] <= service_area_threshold))
    incidents_c['wellness'] = [0] * len(incidents_c)
    incidents_c.loc[incidents_c['false_positive'] == True, 'wellness'] = -1
    incidents_c.loc[incidents_c['false_negative'] == True, 'wellness'] = 1
    incidents_c = incidents_c[~incidents_c['wellness'].isna()]

    # add demo info - census tract
    # incidents_c = pp_i.add_geo_unit(incidents_c, dir_tract_boundaries, ['NAME'])
    # demographic = pp_i.import_demographic(
    #     dir_income_tract, ['S1901_C01_012E'],  # Households!!Estimate!!Median income (dollars)
    #     # dir_edu, ['S1501_C02_015E'],  # Percent!!Estimate!!Percent bachelor's degree or higher
    #     # dir_population, ['DP05_0021E'],  # Estimate!!SEX AND AGE!!65 years and over
    #     # dir_population,[
    #     #     'DP05_0004E', 'DP05_0005E'
    #     # ],  # Estimate!!SEX AND AGE!!Under 5 years | Estimate!!SEX AND AGE!!5 to 9 years
    #     # dir_population, [
    #     #     'DP05_0059PE'
    #     # ],  # Percent!!RACE!!Race alone or in combination with one or more other races!!Total population!!White
    #     ['9901'],
    # )
    # incidents_c = incidents_c.merge(demographic, how='left', left_on='NAME', right_on='tract_name')

    # add demo info - block groups
    incidents_c = pp_i.add_geo_unit(incidents_c, dir_bg_boundaries, ['TRACTCE', 'BLKGRPCE'])
    demo = pp_i.import_demographic(
        dir_income_bg, ['B19013_001E'],  # Median household income
        # dir_edu_bg, ['above_bch_ratio'],  # Education attainment
        # dir_minority_bg, ['white_ratio'],  # Minority
        # dir_age_bg, ['5_to_65_ratio'],  # Age
        ['9901'],
    )
    incidents_c = pp_i.merge_incidents_demographic_bg(incidents_c, demo)
    incidents_c = incidents_c[~incidents_c['demographic_value'].isna()]

    # split
    incidents_f = incidents_c[incidents_c['period_label'] != '']
    incidents_n = incidents_c[incidents_c['period_label'] == '']

    # demo w geo
    demo = pp_i.merge_demographic_geo_bg(demo, dir_bg_boundaries)

    # aggr to geo units
    g_units_f = pp_i.aggr_incidents_geo(incidents_f, period_dict, dir_bg_boundaries)
    g_units_n = pp_i.aggr_incidents_geo(incidents_n, period_dict, dir_bg_boundaries)

    # outliers
    g_units_f = pp_i.delete_outlier_mahalanobis(g_units_f, ['demographic_value', 'diff_travel'])
    g_units_n = pp_i.delete_outlier_mahalanobis(g_units_n, ['demographic_value', 'diff_travel'])

    return incidents_f, incidents_n, demo, g_units_f, g_units_n


def shift_test(gdf_1, gdf_2, reg_b_1, reg_b_2, reg_s_1, reg_s_2):
    gdf_1['s_dependency'] = reg_s_1.q
    gdf_2['s_dependency'] = reg_s_2.q

    demo_cov = reg.reg_SUR(
        gdf_1[gdf_1['id'].isin(gdf_2['id'])][
            ['demographic_value', 's_dependency']
        ].values,
        gdf_1[gdf_1['id'].isin(gdf_2['id'])]['diff_travel'].values,
        gdf_2[gdf_2['id'].isin(gdf_1['id'])][
            ['demographic_value', 's_dependency']
        ].values,
        gdf_2[gdf_2['id'].isin(gdf_1['id'])]['diff_travel'].values,
    )
    score = reg.reg_z_score_4_compared_coeff(
        reg_b_1.betas[1][0], reg_b_2.betas[1][0], reg_b_1.std_err[1], reg_b_2.std_err[1], demo_cov,
    )
    print(f'z score for the one-side test is {score}.')
    return score


if __name__ == "__main__":

    ########
    # save_inundated_roads()
    # save_rescue_data()
    # build_full_graph_arcgis()

    # for i in [1, 2, 3, 4, 5]:
    #     calculate_all_routes(op=i)

    # ######## Results 1
    # incidents = calculate_incidents_with_gis_travel_time(op=1)
    # _, _, _, geo_units_flood, geo_units_normal = calculate_incidents_metrics(incidents)
    #
    # vis.reg_spatial_lag(
    #     geo_units_flood, "demographic_value", "diff_travel",
    #     5,  # income
    #     # 3,  # edu
    #     # 3,  # race
    #     # 3,  # age
    # )
    #
    # vis.scatter_demo_vs_error(
    #     geo_units_flood, 'demographic_value', 'diff_travel',
    #     yaxis='Travel time estimation error (s)',
    #     # income
    #     xaxis='Median household income (USD) ',
    #     xrange=[20000, 140000],
    #     # reg_line=[0.00345, -504.02038],  # regular reg
    #     reg_line=[0.00436, -736.55505],  # spatial reg
    #     # reg_line=[0.00274, -574.94337],  # spatial reg after origin correction
    #     # reg_line=[0.00365, -564.69646],  # spatial reg after daily congestion
    #     # # edu
    #     # xaxis='Percentage of Bachelor\'s degree and higher',
    #     # xrange=[0, 0.8],
    #     # reg_line=[280.29056, -494.24028],  # spatial reg
    #     # # race
    #     # xaxis='Percentage of minority',
    #     # xrange=[0.2, 1],
    #     # reg_line=[146.02351, -524.40382],  # spatial reg
    #     # # age
    #     # xaxis='Percentage of population aged 5 to 65',
    #     # xrange=[0.5, 1.05],
    #     # reg_line=[-255.38392, 78.97383],  # spatial reg
    # )
    #
    # vis.scatter_demo_vs_error(
    #     geo_units_normal, 'demographic_value', 'diff_travel',
    #     yaxis='Travel time estimation error (s)',
    #     # income
    #     xaxis='Median household income (USD) ',
    #     xrange=[20000, 140000],
    #     # # edu
    #     # xaxis='Percentage of Bachelor\'s degree and higher',
    #     # xrange=[0, 0.8],
    #     # # race
    #     # xaxis='Percentage of minority',
    #     # xrange=[0.2, 1],
    #     # # age
    #     # xaxis='Percentage of population aged 5 to 65',
    #     # xrange=[0.5, 1.05],
    #     # color='#B6E1F2', size=16.5, height=300,
    # )
    # #######

    ##### Results 2
    # reg
    incidents_op1 = calculate_incidents_with_gis_travel_time(op=1)
    _, _, _, geo_units_f_op1, _ = calculate_incidents_metrics(incidents_op1)
    incidents_op2 = calculate_incidents_with_gis_travel_time(op=2)
    _, _, _, geo_units_f_op2, _ = calculate_incidents_metrics(incidents_op2)

    reg.reg_spatial_lag(
        geo_units_f_op1, w_lag=1, method='ML', weight_method='Queen', #spillover=True
    )
    reg.reg_spatial_lag(
        geo_units_f_op2, w_lag=1, method='ML', weight_method='Queen', #spillover=True
    )
    reg.reg_shift_test_bootstrapping(
        geo_units_f_op1, geo_units_f_op2,
        'ML',
        weight_method='Queen',
        # k1=4, k2=4, weight_method='KNN',
        w_lag=1,
        # spillover=True
    )
    reg.reg_shift_test_regime(geo_units_f_op1, geo_units_f_op2, 1, w_lag=1, method='ML')

    # micro-scale examination
    rescue_station = pp_s.import_rescue_station(dir_rescue_station_n_nearest_geo)
    incidents = pp_i.import_incidents_add_info(
        dir_incidents, rescue_station, period_dict, routing_nearest=dir_incidents_routing_nearest,
    )
    incidents_shift = incidents[incidents['Number_nearest'] != incidents['Number_actual']]
    incidents_f = incidents_shift[~incidents_shift['period_actual'].isna()]
    incidents_n = incidents_shift[incidents_shift['period_actual'].isna()]

    incidents_f.loc[:, ['if_nearest_occupied', 'if_nearest_closed']] = incidents_f.apply(
        lambda x: pp_s.check_occupation(
            x, station_col='Number_nearest', time_col='Call Date and Time', incidents=incidents,
        ),
        axis=1, result_type='expand'
    ).values
    vis.map_origin_shift(incidents_f, rescue_station, mode='actual')
    ######

    ###### Results 3
    # reg
    incidents_op1 = calculate_incidents_with_gis_travel_time(op=1)
    _, _, _, geo_units_f_op1, _ = calculate_incidents_metrics(incidents_op1)
    incidents_op3 = calculate_incidents_with_gis_travel_time(op=3)
    _, _, _, geo_units_f_op3, _ = calculate_incidents_metrics(incidents_op3)



    # vis
    for l in period_short:
        if os.path.exists(f'{dir_road_cube6_out_c}/{l}_FDBKNET_LINK.dbf'):
            road_segment = pp_r.merge_road_info_VDOT(
                dir_road_cube6, f'{dir_road_cube6_out_c}/{l}_FDBKNET_LINK.dbf'
            )
            vis.map_road_speed(road_segment, 'TIME_1')
    ######

    ###### Results 4
    # reg
    incidents_op1 = calculate_incidents_with_gis_travel_time(op=1)
    _, _, _, geo_units_f_op1, _ = calculate_incidents_metrics(incidents_op1)
    incidents_op4 = calculate_incidents_with_gis_travel_time(op=4)
    _, _, _, geo_units_f_op4, _ = calculate_incidents_metrics(incidents_op4)

    reg.reg_spatial_lag(geo_units_f_op1, 4, method='ML')
    reg.reg_spatial_lag(geo_units_f_op4, 4, method='ML')
    reg.reg_shift_test_bootstrapping(geo_units_f_op1, geo_units_f_op1, 4, 4, method='ML')
    reg.reg_shift_test_regime(geo_units_f_op1, geo_units_f_op4, 4, method='ML')

    # vis
    for l in period_short:
        f_dir = f'{dir_road_cube6_out_d}_{l}/{l}_FDBKNET_LINK.dbf'
        if os.path.exists(f_dir):
            road_segment = pp_r.merge_road_info_VDOT(
                dir_road_cube6, f_dir
            )
            vis.map_road_speed(road_segment, 'TIME_1')

    incidents = calculate_incidents_with_gis_travel_time(op=4)
    _, _, _, geo_units_flood, _ = calculate_incidents_metrics(incidents)
    vis.scatter_demo_vs_error(
        geo_units_flood, 'demographic_value', 'diff_travel',
        yaxis='Travel time estimation error (s)',
        xaxis='Median household income (USD) ',
        xrange=[20000, 140000],
    )

    ###### Results 5: origin shift + flood congestion
    incidents_op1 = calculate_incidents_with_gis_travel_time(op=1)
    _, _, _, geo_units_f_op1, _ = calculate_incidents_metrics(incidents_op1)
    incidents_op5 = calculate_incidents_with_gis_travel_time(op=5)
    _, _, _, geo_units_f_op5, _ = calculate_incidents_metrics(incidents_op5)

    reg.reg_shift_test_bootstrapping(
        geo_units_f_op1, geo_units_f_op5,
        'ML',
        weight_method='Queen',
        # k1=3, k2=3, weight_method='KNN',
        w_lag=1,
        # spillover=True
    )

    # ###### Other vis
    # vis.map_error(geo_units_flood, 'diff_travel')
    # vis.map_demo(geo_units_flood, 'demographic_value', 'Median house income (US dollar)')
    #
    # vis.line_cut_n_ave_wellness(
    #     geo_units_normal,
    #     [round(x * 0.05, 2) for x in range(2, 11)],
    #     'range',
    # )

    ###### Other data processing: edit the net in CUBE
    # save_inundated_roads_4_sim()
    nets = pp_r.merge_inundation_info_2_net(
        dir_road_cube7, dir_road_cube6_inundated, period_dict, period_split,
        'cubedb__Master_Network_CUBE7__link'
    )
    updated_nets = pp_r.edit_net_using_inundation(nets)
    pp_r.edit_net_sqlite(
        dir_road_cube7, dir_road_cube7_inundated, updated_nets, period_split,
        'cubedb__Master_Network_CUBE7__link',
    )



    ########

    print()
