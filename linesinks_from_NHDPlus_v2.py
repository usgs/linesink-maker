'''
develop GFLOW stream network from NHDPlus information
all input shapefiles have to be in the same projected (i.e. ft. or m, not deg) coordinate system
'''

import numpy as np
import os
import sys
sys.path.append('D:/ATLData/Documents/GitHub/GIS_utils/')
import GISio
from shapely.geometry import Polygon, LineString
from shapely.ops import cascaded_union
import math

working_dir = 'D:/ATLData/GFL files/Nicolet/new_linesinks'

# model domain info
farfield = 'M:/GroundWater_Systems/USFS/Nicolet/ARC/Nicolet_FF.shp'
nearfield = 'M:/GroundWater_Systems/USFS/Nicolet/ARC/Nicolet_NF.shp'

# output linesink file
outfile_basename = os.path.join(working_dir, 'Nicolet')
error_reporting = os.path.join(working_dir, 'linesinks_from_NHDPlus_v2_errors.txt')

# merged NHD files for major drainage areas in domain
flowlines = os.path.join(working_dir, 'NHDPlus_0407_NAD27_utm16.shp')
elevslope = 'D:/ATLData/BadRiver/BCs/SFR_v4_new_streams/elevslope.dbf'
PlusFlowVAA = 'D:/ATLData/BadRiver/BCs/SFR_v4_new_streams/PlusFlowlineVAA_0407Merge.dbf'
waterbodies = None
preprocess = False # reproject and clip input data from NHD (incomplete option)

# preprocessed files
DEM = 'D:/ATLData/GFL files/Nicolet/basemaps/10mdem' # GDAL raster projected to same coordinate system
DEM_zmult = 1/0.3048 # multiplier to convert DEM elevation units to model elevation units
flowlines_clipped = os.path.join(working_dir, 'flowlines_clipped.shp')
waterbodies_clipped = 'D:/ATLData/GFL files/Nicolet/new_linesinks/NHDwaterbodies.shp'
wb_centroids_w_elevations = waterbodies_clipped[:-4] + '_points.shp' # elevations extracted during preprocessing routine
elevs_field = 'DEM_m' # field in wb_centroids_w_elevations containing elevations

# option to split out linesinks by HUC, writing one lss file per HUC
split_by_HUC = True # GFLOW may not be able import if the lss file is too big
HUC_shp = 'D:/ATLData/GFL files/Nicolet/basemaps/north_south.shp'
HUC_name_field = 'HUC'

# set max error tolerance for simplifying linework 
# (largest distance between original lines and simplified lines, in projection units)
nearfield_tolerance = 200
farfield_tolerance = 500
min_farfield_order = 2 # minimum stream order to retain in farfield
min_waterbody_size = 1.0 # minimum sized waterbodies to retain (km2)

z_mult = 1/(2.54*12) # elevation units multiplier (from NHDPlus cm to model units)

# GFLOW resistance parameters
resistance = 0.3 # (days); c in documentation
H = 100 # aquifer thickness in model units
k = 10 # hydraulic conductivity of the aquifer in model units
lmbda = np.sqrt(10 * 100 * 0.3)
ScenResistance = 'Rlinesink' # one global parameter for now


### Functions #############################

def w_parameter(B, lmbda):
    # see Haitjema 2005, "Dealing with Resistance to Flow into Surface Waters"
    if lmbda <= 0.1 * B:
        w = lmbda
    elif 0.1 * B < lmbda < 2 * B:
        w = lmbda * np.tanh(B / (2 * lmbda))
    else:
        w = B /2
    return w


def width_from_arboate(arbolate, lmbda):
    # estimate stream width in feet from arbolate sum in meters
    # see LMB report, Appendix 2, p 266.
    estwidth = 0.1193 * math.pow(1000 * arbolate, 0.5032)
    w = 2 * w_parameter(estwidth, lmbda) # assumes stream is rep. by single linesink
    return w


def lake_width(area, total_line_length, lmbda):
    # estimate conductance width from lake area and length of flowlines running through it
    if total_line_length > 0:
        estwidth = 1000 * (area / total_line_length) / 0.3048  # (km2/km)*(ft/km)
    else:
        estwidth = np.sqrt(area / np.pi) * 1000 / 0.3048  # (km)*(ft/km)

    # see Haitjema 2005, "Dealing with Resistance to Flow into Surface Waters"
    # basically if only larger lakes are simulated (e.g., > 1 km2), w parameter will be = lambda
    # this assumes that GFLOW's lake package will not be used
    w = w_parameter(estwidth, lmbda)
    return w # feet


def name(x):
    # convention to name linesinks from NHDPlus
    if x.GNIS_NAME:
        # reduce name down with abbreviations
        abb = {'Branch': 'Br',
               'Creek': 'Crk',
               'East': 'E',
               'Flowage': 'Fl',
               'Lake': 'L',
               'North': 'N',
               'Pond': 'P',
               'Reservoir': 'Res',
               'River': 'R',
               'South': 'S',
               'West': 'W'}
               
        name = '{} {}'.format(x.name, x.GNIS_NAME)
        for k, v in abb.iteritems():
            name = name.replace(k, v)
    else:
        name = '{} unnamed'.format(x.name)
    return name


def write_lss(df, outfile):
    '''
    write GFLOW linesink XML (lss) file from dataframe df
    '''
    # global inputs
    depth = 3 # streambed thickness
    DefaultResistance = 0.3
    ComputationalUnits = 'Feet' # 'Feet' or 'Meters'; for XML output file
    BasemapUnits = 'Meters'
    
    nlines = sum([len(p)-1 for p in df.ls_coords])
    
    print 'writing {} lines to {}'.format(nlines, outfile)
    ofp = open(outfile,'w')
    ofp.write('<?xml version="1.0"?>\n')
    ofp.write('<LinesinkStringFile version="1">\n')
    ofp.write('\t<ComputationalUnits>{}</ComputationalUnits>\n\t<BasemapUnits>{}</BasemapUnits>\n\n'.format(ComputationalUnits, BasemapUnits))

    for comid in df.index:
        ofp.write('\t<LinesinkString>\n')
        ofp.write('\t\t<Label>{}</Label>\n'.format(df.ix[comid, 'ls_name']))
        ofp.write('\t\t<HeadSpecified>1</HeadSpecified>\n')
        ofp.write('\t\t<StartingHead>{:.2f}</StartingHead>\n'.format(df.ix[comid, 'maxElev']))
        ofp.write('\t\t<EndingHead>{:.2f}</EndingHead>\n'.format(df.ix[comid, 'minElev']))
        ofp.write('\t\t<Resistance>{}</Resistance>\n'.format(df.ix[comid, 'resistance']))
        ofp.write('\t\t<Width>{:.2f}</Width>\n'.format(df.ix[comid, 'width']))
        ofp.write('\t\t<Depth>{:.2f}</Depth>\n'.format(resistance))
        ofp.write('\t\t<Routing>{}</Routing>\n'.format(df.ix[comid, 'routing']))
        ofp.write('\t\t<EndStream>{}</EndStream>\n'.format(df.ix[comid, 'end_stream']))
        ofp.write('\t\t<OverlandFlow>0</OverlandFlow>\n')
        ofp.write('\t\t<EndInflow>0</EndInflow>\n')
        ofp.write('\t\t<ScenResistance>{}</ScenResistance>\n'.format(df.ix[comid, 'ScenResistance']))
        ofp.write('\t\t<Drain>0</Drain>\n')
        ofp.write('\t\t<ScenFluxName>__NONE__</ScenFluxName>\n')
        ofp.write('\t\t<Gallery>0</Gallery>\n')
        ofp.write('\t\t<TotalDischarge>0</TotalDischarge>\n')
        ofp.write('\t\t<InletStream>0</InletStream>\n')
        ofp.write('\t\t<OutletStream>0</OutletStream>\n')
        ofp.write('\t\t<OutletTable>__NONE__</OutletTable>\n')
        ofp.write('\t\t<Lake>0</Lake>\n')
        ofp.write('\t\t<Precipitation>0</Precipitation>\n')
        ofp.write('\t\t<Evapotranspiration>0</Evapotranspiration>\n')
        ofp.write('\t\t<Farfield>{:.0f}</Farfield>\n'.format(df.ix[comid, 'farfield']))
        ofp.write('\t\t<chkScenario>true</chkScenario>\n') # include linesink in PEST 'scenarios'
        ofp.write('\t\t<AutoSWIZC>{:.0f}</AutoSWIZC>\n'.format(df.ix[comid, 'AutoSWIZC']))
        ofp.write('\t\t<DefaultResistance>{:.2f}</DefaultResistance>\n'.format(DefaultResistance))
        ofp.write('\t\t<Vertices>\n')
        
        # now write out linesink vertices
        for x, y in df.ix[comid, 'ls_coords']:
            ofp.write('\t\t\t<Vertex>\n')
            ofp.write('\t\t\t\t<X> {:.2f}</X>\n'.format(x))
            ofp.write('\t\t\t\t<Y> {:.2f}</Y>\n'.format(y))
            ofp.write('\t\t\t</Vertex>\n')
            
        ofp.write('\t\t</Vertices>\n')
        ofp.write('\t</LinesinkString>\n\n')
    ofp.write('</LinesinkStringFile>')
    ofp.close()


### Main Program #############################

if preprocess: # incomplete; does not include projection
    import arcpy
    # initialize the arcpy environment
    arcpy.env.workspace = working_dir
    arcpy.env.overwriteOutput = True
    arcpy.env.qualifiedFieldNames = False
    arcpy.CheckOutExtension("spatial") # Check spatial analyst license

    # clip NHD flowlines and waterbodies to domain
    arcpy.Clip_analysis(flowlines, farfield, flowlines_clipped)
    arcpy.Clip_analysis(waterbodies, farfield, waterbodies_clipped)

    # convert farfield polygon to donut by erasing the nearfield area (had trouble doing this with shapely)
    arcpy.Erase_analysis(farfield, nearfield, os.path.join(working_dir, 'ff_cutout.shp'))

    # get the elevations of all NHD Waterbody features from DEM (needed for isolated lakes)
    arcpy.FeatureToPoint_management(waterbodies_clipped, wb_centroids_w_elevations)
    arcpy.sa.ExtractMultiValuesToPoints(wb_centroids_w_elevations, [[DEM, elevs_field]])


# open error reporting file
efp = open(error_reporting, 'w')

print '\nAssembling input...'
# read linework shapefile into pandas dataframe
df = GISio.shp2df(flowlines_clipped, geometry=True, index='COMID')
elevs = GISio.shp2df(elevslope, index='COMID')
pfvaa = GISio.shp2df(PlusFlowVAA, index='COMID')
wbs = GISio.shp2df(waterbodies_clipped, index='COMID', geometry=True)

# check for MultiLineStrings / MultiPolygons and drop them (these are features that were fragmented by the boundaries)
mls = [i for i in df.index if 'multi' in df.ix[i]['geometry'].type.lower()]
df = df.drop(mls, axis=0)
# get multipolygons using iterator; for some reason the above approach didn't work with the wbs dataframe
mpoly_inds = [i for i, t in enumerate(wbs['geometry']) if 'multi' in t.type.lower()]
wbs = wbs.drop(wbs.index[mpoly_inds], axis=0)

# join NHD tables to lines
lsuffix = 'fl'
df = df.join(elevs, how='inner', lsuffix=lsuffix, rsuffix='elevs')
df = df.join(pfvaa, how='inner', lsuffix=lsuffix, rsuffix='pfvaa')

# read in nearfield and farfield boundaries
nf = GISio.shp2df(nearfield, geometry=True)
nfg = nf.iloc[0]['geometry'] # polygon representing nearfield
ff = GISio.shp2df(os.path.join(working_dir, 'ff_cutout.shp'), geometry=True)
ffg = ff.iloc[0]['geometry'] # shapely geometry object for farfield (polygon with interior ring for nearfield)

print '\nidentifying farfield and nearfield linesinks...'
df['farfield'] = [line.intersects(ffg) and not line.intersects(nfg) for line in df['geometry']]
wbs['farfield'] = [poly.intersects(ffg) for poly in wbs['geometry']]

print 'removing farfield streams lower than {} order...'.format(min_farfield_order)
df = df.drop(df.index[np.where(df['farfield'] & (df['StreamOrde'] < min_farfield_order))], axis=0)

print 'dropping waterbodies that are not lakes larger than {}...'.format(min_waterbody_size)
wbs = wbs.drop(wbs.index[np.where((wbs['AREASQKM'] < min_waterbody_size) | (wbs['FTYPE'] != 'LakePond'))], axis=0)

print 'merging waterbodies with coincident boundaries...'
for wb_comid in wbs.index:
    overlapping = wbs.ix[[wbs.ix[wb_comid, 'geometry'].intersects(r) \
                                                        for r in wbs.geometry]]
    basering_comid = overlapping.sort('FTYPE').index[0] # sort to prioritize features with names
    # two or more shapes in overlapping signifies a coincident boundary
    if len(overlapping > 1):
        merged = cascaded_union([r for r in overlapping.geometry]).exterior
        wbs.ix[wb_comid, 'geometry'] = Polygon(merged) # convert from linear ring back to polygon (for next step)
        wbs = wbs.drop([wbc for wbc in overlapping.index if wbc != basering_comid]) # only keep merged feature

# swap out polygons in lake geometry column with the linear rings that make up their exteriors
print 'converting lake exterior polygons to lines...'
wbs['geometry'] = wbs['geometry'].apply(lambda x: x.exterior)

print 'merging flowline and waterbody datasets...'
df = df.append(wbs)

print 'simplifying geometries...'
# simplify line and waterbody geometries
#(see http://toblerity.org/shapely/manual.html)
df['geometry_nf'] = df['geometry'].map(lambda x: x.simplify(nearfield_tolerance))
df['geometry_ff'] = df['geometry'].map(lambda x: x.simplify(farfield_tolerance))


print 'Assigning attributes for GFLOW input...'

# convert geometries to coordinates
def xy_coords(x):
    xy = zip(x.coords.xy[0], x.coords.xy[1])
    return xy
df.loc[np.invert(df['farfield']), 'ls_coords'] = df['geometry_nf'].apply(xy_coords) # nearfield coordinates
df.loc[df['farfield'], 'ls_coords'] = df['geometry_ff'].apply(xy_coords) # farfield coordinates

# loops or braids in NHD linework can result in duplicate lines after simplification
# create column of line coordinates converted to strings
df['ls_coords_str'] = [''.join(map(str, coords)) for coords in df.ls_coords]
df = df.drop_duplicates('ls_coords_str') # drop rows from dataframe containing duplicates
df = df.drop('ls_coords_str', axis=1)

# routing
df['routing'] = len(df)*[1]
df.loc[df['farfield'], 'routing'] = 0 # turn off all routing in farfield (conversely, nearfield is all routed)


# linesink elevations (lakes won't be populated yet)
min_elev_col = [c for c in df.columns if 'minelev' in c.lower()][0]
max_elev_col = [c for c in df.columns if 'maxelev' in c.lower()][0]
df['minElev'] = df[min_elev_col] * z_mult
df['maxElev'] = df[max_elev_col] * z_mult
df['dStage'] = df['maxElev'] - df['minElev']


# record up and downstream comids for lines
lines = [l for l in df.index if l not in wbs.index]
df['dncomid'] = len(df)*[[]]
df['upcomids'] = len(df)*[[]]
df.ix[lines, 'dncomid'] = [list(df[df['Hydroseq'] == df.ix[i, 'DnHydroseq']].index) for i in lines]
df.ix[lines, 'upcomids'] = [list(df[df['DnHydroseq'] == df.ix[i, 'Hydroseq']].index) for i in lines]


# read in elevations for NHD waterbodies (from preprocessing routine; needed for isolated lakes)
wb_elevs = GISio.shp2df(wb_centroids_w_elevations, index='COMID')
wb_elevs = wb_elevs[elevs_field] * DEM_zmult

# identify lines that represent lakes
# get elevations, up/downcomids, and total lengths for those lines
# assign attributes to lakes, then drop the lines

for wb_comid in wbs.index:

    lines = df[df['WBAREACOMI'] == wb_comid]

    # isolated lakes have no overlapping lines and no routing
    if len(lines) == 0:
        df.ix[wb_comid, 'maxElev'] = wb_elevs[wb_comid]
        df.ix[wb_comid, 'minElev'] = wb_elevs[wb_comid] - 0.01
        df.ix[wb_comid, 'routing'] = 0
        continue
    # get upcomids and downcomid for lake,
    # by differencing all up/down comids for lines in lake, and comids in the lake

    #df.ix[wb_comid, 'upcomids'] = list(set([c for l in lines.upcomids for c in l]) - set(lines.index))
    #df.ix[wb_comid, 'dncomid'] = list(set([c for l in lines.dncomid for c in l]) - set(lines.index))
    df.set_value(wb_comid, 'upcomids', list(set([c for l in lines.upcomids for c in l]) - set(lines.index)))
    df.set_value(wb_comid, 'dncomid', list(set([c for l in lines.dncomid for c in l]) - set(lines.index)))
    if np.min(lines.minElev) == np.nan or np.min(lines.minElev) == np.nan:
        j=2
    df.ix[wb_comid, 'minElev'] = np.min(lines.minElev)
    df.ix[wb_comid, 'maxElev'] = np.min(lines.maxElev)

    # update all up/dn comids in lines dataframe that reference the lines inside of the lakes
    # (replace those references with the comids for the lakes)
    for comid in lines.index:
        df.ix[df.FTYPE != 'LakePond', 'dncomid'] = [[wb_comid if v == comid else v for v in l] for l in df[df.FTYPE != 'LakePond'].dncomid]
        df.ix[df.FTYPE != 'LakePond', 'upcomids'] = [[wb_comid if v == comid else v for v in l] for l in df[df.FTYPE != 'LakePond'].upcomids]

    # get total length of lines representing lake (used later to estimate width)
    df.ix[wb_comid, 'total_line_length'] = np.sum(lines.LengthKM)

    # modifications to routed lakes
    if df.ix[wb_comid, 'routing'] == 1:

        # enforce gradient; update elevations in downstream comids
        if df.ix[wb_comid, 'minElev'] == df.ix[wb_comid, 'maxElev']:
            df.ix[wb_comid, 'minElev'] -= 0.01
            for dnid in df.ix[wb_comid, 'dncomid']:
                df.ix[dnid, 'maxElev'] -= 0.01

        # move begining/end coordinate of linear ring representing lake to outlet location (to ensure correct routing)
        # some routed lakes may not have an outlet
        if len(df.ix[wb_comid, 'dncomid']) > 0:
            outlet_coords = df.ix[df.ix[wb_comid, 'dncomid'][0], 'ls_coords'][0]

            # find vertex closest to outlet
            X, Y = np.ravel(df.ix[wb_comid, 'geometry_nf'].coords.xy[0]), np.ravel(df.ix[wb_comid, 'geometry_nf'].coords.xy[1])
            dX, dY = X - outlet_coords[0], Y - outlet_coords[1]
            closest_ind = np.argmin(np.sqrt(dX**2 + dY**2))

            # make new set of vertices that start and end at outlet location (and only include one instance of previous start/end!)
            new_coords = df.ix[wb_comid, 'ls_coords'][closest_ind:] + df.ix[wb_comid, 'ls_coords'][1:closest_ind+1]
            df.set_value(wb_comid, 'ls_coords', new_coords)

    # drop the lines representing the lake from the lines dataframe
    df = df.drop(lines.index)


print '\nmerging or splitting lines with only two vertices...'
# find all routed comids with only 1 line; merge with neighboring comids
# (GFLOW requires two lines for routed streams)

def bisect(coords):
    # add vertex to middle of single line segment
    coords = np.array(coords)
    mid = 0.5 * (coords[0] + coords[1])
    new_coords = map(tuple, [coords[0], mid, coords[1]])
    return new_coords

df['nlines'] = [len(coords) for coords in df.ls_coords]
#comids1 = list(df[(df['nlines'] < 3) & (df['routing'] == 1)]['COMID'+lsuffix])
comids1 = list(df[(df['nlines'] < 3) & (df['routing'] == 1)].index)
efp.write('\nunrouted comids of length 1 that were dropped:\n')
for comid in comids1:

    # get up and down comids/elevations; only consider upcomid/downcomids that are streams (exclude lakes)
    #upcomids = [c for c in df[df.index == comid]['upcomids'].item() if c not in wbs.index]
    #dncomid = [c for c in df[df.index == comid]['dncomid'].item() if c not in wbs.index]
    upcomids = [c for c in df[df.index == comid]['upcomids'].item()] # allow lakes and lines to be merged (if their vertices coincide)
    dncomid = [c for c in df[df.index == comid]['dncomid'].item()]
    merged = False
    #if comid == 13392281 or 13392281 in upcomids or 13392281 in dncomid:

    # first try to merge with downstream comid
    if len(dncomid) > 0:
        # only merge if start of downstream comid coincides with last line segment
        if df.ix[comid].ls_coords[-1] == df.ix[dncomid[0]].ls_coords[0]:
            new_coords = df.ix[comid].ls_coords + df.ix[dncomid[0]].ls_coords[1:]
            df.set_value(dncomid[0], 'ls_coords', new_coords) # update coordinates in dncomid
            df.loc[dncomid, 'maxElev'] = df.ix[comid].maxElev # update max elevation
            #df['dncomid'].replace(comid, dncomid)
            df = df.drop(comid, axis=0)
            #if dncomid in comids1: comids1.remove(dncomid) # for now, no double merges
            # double merges degrade vertical elevation resolution,
            # but more merging may be necessary to improve performance of GFLOW's database
            replacement = dncomid[0]
            merged = True
        else: # split it
            new_coords = bisect(df.ix[comid].ls_coords)
            df.set_value(comid, 'ls_coords', new_coords)

    elif len(upcomids) > 0: # merge into first upstream comid; then drop
        for uid in upcomids:
            # check if upstream end coincides with current start
            if df.ix[uid].ls_coords[-1] == df.ix[comid].ls_coords[0]:
                new_coords = df.ix[uid].ls_coords + df.ix[comid].ls_coords[1:]
                df.set_value(uid, 'ls_coords', new_coords) # update coordinates in upcomid
                df.loc[uid, 'minElev'] = df.ix[comid].minElev # update min elevation
                #df['upcomids'].replace(comid, uid) # update any references to current comid
                df = df.drop(comid, axis=0)
                #if uid in comids1: comids1.remove(uid)
                replacement = uid
                merged = True
                break
            else: # split it (for Nicolet, no linesinks were in this category)
                continue
        if not merged:
            new_coords = bisect(df.ix[comid].ls_coords)
            df.set_value(comid, 'ls_coords', new_coords)

    else: # the segment is not routed to any up/dn comids that aren't lakes
        # split it for now (don't want to drop it if it connects to a lake)
        new_coords = bisect(df.ix[comid].ls_coords)
        df.set_value(comid, 'ls_coords', new_coords)

    if merged:
        # update any references to current comid (clunkly because each row is a list)
        df['dncomid'] = [[replacement if v == comid else v for v in l] for l in df['dncomid']]
        df['upcomids'] = [[replacement if v == comid else v for v in l] for l in df['upcomids']]


print "adjusting elevations for comids with zero-gradient..."

comids0 = list(df[df['dStage'] == 0].index)
efp.write('\nzero-gradient errors:\n')
efp.write('comid, upcomids, downcomid, elevmax, elevmin\n')
zerogradient = []

for comid in comids0:

    # get up and down comids/elevations
    upcomids = df[df.index == comid]['upcomids'].item()
    upelevsmax = [df[df.index == uid]['maxElev'].item() for uid in upcomids]
    dncomid = df[df.index == comid]['dncomid'].item()
    dnelevmin = [df[df.index == dnid]['minElev'].item() for dnid in dncomid]

    # adjust elevations for zero gradient comid if there is room
    if len(upcomids) == 0:
        df.loc[comid, 'maxElev'] += 0.01
    elif len(dncomid) == 0:
        df.loc[comid, 'minElev'] -= 0.01
    elif len(upcomids) > 0 and np.min(upelevsmax) > df.ix[comid, 'maxElev']:
        df.loc[comid, 'maxElev'] = 0.5 * (df.ix[comid, 'maxElev'] + np.min(upelevsmax))
    elif len(dncomid) > 0 and dnelevmin < df.ix[comid, 'minElev']:
        df.loc[comid, 'minElev'] = 0.5 * (df.ix[comid, 'minElev'] + dnelevmin)

    # otherwise, downstream and upstream comids are also zero gradient; report to error file
    else:
        farfield = df.ix[comid, 'farfield']
        if not farfield:
            efp.write('{},{},{},{:.2f},{:.2f}\n'.format(comid, upcomids, dncomid, df.ix[comid, 'maxElev'].item(),
                      df.ix[comid, 'minElev'].item()))
            #df.loc[comid, 'routing'] = 0
            #just increase the max elev slightly to get around zero-gradient error
            df.loc[comid, 'maxElev'] += 0.01
            zerogradient.append(comid)

print "\nWarning!, the following comids had zero gradients:\n{}".format(zerogradient)
print "routing for these was turned off. Elevations must be fixed manually"


# end streams
# evaluate whether downstream segment is in farfield
downstream_ff = []
for i in range(len(df)):
    try:
        dff = df.ix[df.iloc[i].dncomid[0], 'farfield'].item()
    except:
        dff = True
    downstream_ff.append(dff)
    
# set segments with downstream segment in farfield as End Segments
df['end_stream'] = len(df) * [0]
df.loc[downstream_ff, 'end_stream'] = 1 # set


# widths for lines
arbolate_sum_col = [c for c in df.columns if 'arbolate' in c.lower()][0]
df['width'] = df[arbolate_sum_col].map(lambda x: width_from_arboate(x, lmbda))

# widths for lakes
df.ix[df['FTYPE'] == 'LakePond', 'width'] = \
    np.vectorize(lake_width)(df.ix[df['FTYPE'] == 'LakePond', 'AREASQKM'], df.ix[df['FTYPE'] == 'LakePond', 'total_line_length'], lmbda)


# resistance
df['resistance'] = resistance
df.loc[df['farfield'], 'resistance'] = 0

# resistance parameter (scenario)
df['ScenResistance'] = ScenResistance
df.loc[df['farfield'], 'ScenResistance'] = '__NONE__'

# linesink location
df.ix[df['FTYPE'] != 'LakePond', 'AutoSWIZC'] = 1 # Along stream centerline
df.ix[df['FTYPE'] == 'LakePond', 'AutoSWIZC'] = 2 # Along surface water boundary


# additional check to drop isolated lines
isolated = [c for c in df.index if len(df.ix[c].dncomid) == 0 and len(df.ix[c].upcomids) == 0 and c not in wbs.index]
df = df.drop(isolated, axis=0)


# also fix any overlapping lines (caused by simplication) by removing the line with a lower arbolate sum
def actually_crosses(A, B, precis=0.0001):
    """A hybrid spatial predicate that determines if two geometries cross on both sides"""
    # from http://gis.stackexchange.com/questions/26443/is-there-a-way-to-tell-if-two-linestrings-really-intersect-in-jts-or-geos
    return (B.crosses(A) and
            B.crosses(A.parallel_offset(precis, 'left')) and
            B.crosses(A.parallel_offset(precis, 'right')))
for comid in df.index:
    crossed = df.ix[[actually_crosses(df.ix[comid, 'geometry'], l) for l in df.geometry]]
    crossed = crossed.append(df.ix[comid]).sort('ArbolateSu', ascending=False)
    # drop all overlapping lines but the largest
    df.drop(crossed.index[1:])


# names
df['ls_name'] = len(df)*[None]
df['ls_name'] = df.apply(name, axis=1)


# compare number of line segments before and after
npoints_orig = sum([len(p)-1 for p in df['geometry'].map(lambda x: x.xy[0])])
npoints_simp = sum([len(p)-1 for p in df.ls_coords])

print '\nnumber of lines in original NHD linework: {}'.format(npoints_orig)
print 'number of simplified lines: {}\n'.format(npoints_simp)


if split_by_HUC:
    print '\nGrouping segments by hydrologic unit...'
    # intersect lines with HUCs; then group dataframe by HUCs
    HUCs_df = GISio.shp2df(HUC_shp, index=HUC_name_field, geometry=True)
    df[HUC_name_field] = len(df)*[None]
    for HUC in HUCs_df.index:
        lines = [line.intersects(HUCs_df.ix[HUC, 'geometry']) for line in df['geometry']]
        df.loc[lines, HUC_name_field] = HUC
    dfg = df.groupby(HUC_name_field)

    # write lines for each HUC to separate lss file
    HUCs = np.unique(df.HUC)
    for HUC in HUCs:
        dfh = dfg.get_group(HUC)
        outfile = '{}_{}.lss.xml'.format(outfile_basename, HUC)
        write_lss(dfh, outfile)
else:
    write_lss(df, '{}.lss.xml'.format(outfile_basename))

  
# write shapefile of results
# convert lists in dn and upcomid columns to strings (for writing to shp)
df['dncomid'] = df['dncomid'].map(lambda x: ' '.join([str(c) for c in x])) # handles empties
df['upcomids'] = df['upcomids'].map(lambda x: ' '.join([str(c) for c in x]))

# recreate shapely geometries from coordinates column; drop all other coords/geometries
df = df.drop([c for c in df.columns if 'geometry' in c], axis=1)
df['geometry'] = df['ls_coords'].map(lambda x: LineString(x))
df = df.drop(['ls_coords'], axis=1)
GISio.df2shp(df, outfile_basename.split('.')[0]+'.shp', 'geometry', flowlines[:-4]+'.prj')

efp.close()
print 'Done!'



'''

the shapely way to create a donut (didn't work)
# create a donut for the farfield by clipping out the nearfield area
nfg, ffg = ff.iloc[0]['geometry'], nf.iloc[0]['geometry']
ff_clip = ffg.difference(nfg)


# compare with actual linesinks from FC Potowatami model
Pot_ls = GISio.shp2df('D:/ATLData/GFL files/Nicolet/overlapping_GFLOW/shps/Potawatomi_Final_lines.shp', geometry=True)
# write back out to shape
GISio.df2shp(df, 'FCP_test_s.shp', 'geometry_s', 'FCP_test.prj')
'''


