#!/usr/bin/env python3

"""build_full_model

Attempt to use a DMD-esque approach to fit a state transition matrix
that maps previous state to next state, thereby modeling/simulating
flight that closely approximates the original real aircraft.

Author: Curtis L. Olson, University of Minnesota, Dept of Aerospace
Engineering and Mechanics, UAV Lab.

"""

import argparse
from math import cos, pi, sin
from matplotlib import pyplot as plt
import numpy as np
from tqdm import tqdm

from rcUAS_flightdata import flight_loader, flight_interp

from lib.constants import d2r, kt2mps
from lib.system_id import SystemIdentification

# command line arguments
parser = argparse.ArgumentParser(description="build full model")
parser.add_argument("flight", help="flight data log")
parser.add_argument("--write", required=True, help="write model file name")
args = parser.parse_args()

sysid = SystemIdentification()

independent_states = [
    "aileron", "abs(aileron)",
    "elevator",
    "rudder", "abs(rudder)",    # flight controls (* qbar)
    "lift", "drag", "thrust",   # (based on throttle, body accel, and body g)
    "bgx", "bgy", "bgz",         # gravity rotated into body frame
    "bay"         # lateral accel in body frame (lift & drag already include bax, baz)
]

dependent_states = [
    "airspeed",                                  # mps
    "sin(alpha)", "sin(beta)", "abs(sin(beta))", # * qbar
    "p", "q", "r"                                # body rates
]

state_names = independent_states + dependent_states
sysid.state_mgr.set_state_names(independent_states, dependent_states)

# load the flight data
path = args.flight
data, flight_format = flight_loader.load(path)

print("imu records:", len(data["imu"]))
print("gps records:", len(data["gps"]))
if "air" in data:
    print("airdata records:", len(data["air"]))
if "act" in data:
    print("actuator records:", len(data["act"]))
if len(data["imu"]) == 0 and len(data["gps"]) == 0:
    print("not enough data loaded to continue.")
    quit()

# dt estimation
print("Estimating median dt from IMU records:")
iter = flight_interp.IterateGroup(data)
last_time = None
dt_data = []
for i in tqdm(range(iter.size())):
    record = iter.next()
    if len(record):
        if "imu" in record:
            imupt = record["imu"]
            if last_time is None:
                last_time = imupt["time"]
            dt_data.append(imupt["time"] - last_time)
            last_time = imupt["time"]
dt_data = np.array(dt_data)
print("IMU mean:", np.mean(dt_data))
print("IMU median:", np.median(dt_data))
imu_dt = float("%.4f" % np.median(dt_data))
print("imu dt:", imu_dt)

sysid.state_mgr.set_dt(imu_dt)
            
print("Parsing flight data log:")
actpt = {}
airpt = {}
navpt = {}
g = np.array( [ 0, 0, -9.81 ] )

coeff = []

# iterate through the flight data log, cherry pick the selected parameters
iter = flight_interp.IterateGroup(data)
for i in tqdm(range(iter.size())):
    record = iter.next()
    if len(record) == 0:
        continue
    if "imu" in record:
        imupt = record["imu"]
        sysid.state_mgr.set_time( imupt["time"] )
        p = imupt["p"]
        q = imupt["q"]
        r = imupt["r"]
        if "p_bias" in navpt:
            p -= navpt["p_bias"]
            q -= navpt["q_bias"]
            r -= navpt["r_bias"]
        sysid.state_mgr.set_gyros(p, q, r)
    if "act" in record:
        actpt = record["act"]
        sysid.state_mgr.set_throttle( actpt["throttle"] )
        sysid.state_mgr.set_flight_surfaces( actpt["aileron"], actpt["elevator"],
                                       actpt["rudder"] )
    if "air" in record:
        airpt = record["air"]
        asi_mps = airpt["airspeed"] * kt2mps
        # add in correction factor if available
        if "pitot_scale" in airpt:
            asi_mps *= airpt["pitot_scale"]
        sysid.state_mgr.set_airdata( asi_mps )
        if "wind_dir" in airpt:
            wind_psi = 0.5 * pi - airpt["wind_dir"] * d2r
            wind_mps = airpt["wind_speed"] * kt2mps
            we = cos(wind_psi) * wind_mps
            wn = sin(wind_psi) * wind_mps
            wd = 0
        else:
            we = 0.0
            wn = 0.0
            wd = 0.0
    if "filter" in record:
        navpt = record["filter"]
        sysid.state_mgr.set_orientation( navpt["phi"], navpt["the"], navpt["psi"] )
        sysid.state_mgr.set_ned_velocity( navpt["vn"], navpt["ve"], navpt["vd"],
                                          wn, we, wd )
    if "gps" in record:
        gpspt = record["gps"]

    if sysid.state_mgr.is_flying():
        sysid.state_mgr.compute_body_frame_values(body_vel=True)
        state = sysid.state_mgr.gen_state_vector()
        #print(sysid.state_mgr.state2dict(state))
        sysid.add_state_vec(state)
        coeff.append( [sysid.state_mgr.alpha, sysid.state_mgr.Cl, sysid.state_mgr.Cd] )

coeff = np.array(coeff)
print("Cd = %.4f" % np.mean(coeff[:,2]))
plt.figure()
plt.plot(coeff[:,0], coeff[:,1], '*', label="alpha vs. Cl")
plt.plot(coeff[:,0], coeff[:,2], '*', label="alpha vs. Cd")
plt.legend()
plt.show()

states = len(sysid.traindata[0])
print("Number of states:", len(sysid.traindata[0]))
print("Input state vectors:", len(sysid.traindata))

sysid.fit()
sysid.analyze()
sysid.save(args.write, imu_dt)

# show an running estimate of dependent states.  Feed the dependent
# estimate forward into next state rather than using the original
# logged value.  This can show the convergence of the estimated
# parameters versus truth (or show major problems in the model.)

est_index_list = sysid.state_mgr.get_state_index( dependent_states )
#print("est_index list:", est_index_list)
est_val = [0.0] * len(dependent_states)
pred = []
v = []
for i in range(len(sysid.traindata)):
    v.extend(sysid.traindata[i])
    v = v[-states:]       # trim old state values if needed
    for j, index in enumerate(est_index_list):
        v[index-states] = est_val[j]
    if len(v) == states:
        #print("A:", A.shape, A)
        #print("v:", np.array(v).shape, np.array(v))
        p = sysid.A @ np.array(v)
        #print("p:", p)
        for j, index in enumerate(est_index_list):
            est_val[j] = p[index-states]
            param = sysid.model["parameters"][index]
            min = param["min"]
            max = param["max"]
            med = param["median"]
            std = param["std"]
            #if est_val[j] < med - 2*std: est_val[j] = med - 2*std
            #if est_val[j] > med + 2*std: est_val[j] = med + 2*std
            if est_val[j] < min: est_val[j] = min
            if est_val[j] > max: est_val[j] = max
        pred.append(p)
Ypred = np.array(pred).T

index_list = sysid.state_mgr.get_state_index( dependent_states )
for j in index_list:
    plt.figure()
    plt.plot(np.array(sysid.traindata).T[j,:], label="%s (orig)" % state_names[j])
    plt.plot(Ypred[j,:], label="%s (pred)" % state_names[j])
    plt.legend()
    plt.show()

