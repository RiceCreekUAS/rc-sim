"""sim

Uses the system model derived by the system_id module to perform a
flight simulation.

Author: Curtis L. Olson, University of Minnesota, Dept of Aerospace
Engineering and Mechanics, UAV Lab.

"""

import json
from math import asin, atan2, cos, sin, sqrt, pi
from matplotlib import pyplot as plt
import numpy as np
from scipy.optimize import least_squares

from lib import quaternion
from lib.constants import d2r, r2d
from lib.state_mgr import StateManager

class Simulator():
    def __init__(self):
        self.A = None
        self.dt = None
        self.trand = None
        self.state_mgr = StateManager()
        self.reset()

    def load(self, model):
        f = open(model, "r")
        model = json.load(f)
        print(model)
        f.close()
        self.params = model["parameters"]
        cols = len(self.params)
        rows = 0
        for param in self.params:
            if param["type"] == "dependent":
                rows += 1
        print("size:", rows, "x", cols)
        self.dt = model["dt"]
        self.A = np.array(model["A"]).reshape(rows, cols)
        print("A:\n", self.A)
        ind_states = []
        dep_states = []
        for param in model["parameters"]:
            if param["type"] == "independent":
                ind_states.append( param["name"] )
            else:
                dep_states.append( param["name"] )
        self.state_mgr.set_state_names( ind_states, dep_states )
        self.state_mgr.set_dt( self.dt )
        
    def reset(self):
        initial_airspeed_mps = 10.0
        self.state_mgr.set_airdata( initial_airspeed_mps )
        self.state_mgr.set_throttle( 0.5 )
        self.state_mgr.set_flight_surfaces( aileron=0.0,
                                            elevator=-0.1,
                                            rudder=0.0 )
        self.airspeed_mps = 0
        self.pos_ned = np.array( [0.0, 0.0, 0.0] )
        self.vel_ned = np.array( [initial_airspeed_mps, 0.0, 0.0] )
        self.state_mgr.set_ned_velocity( self.vel_ned[0],
                                         self.vel_ned[1],
                                         self.vel_ned[2],
                                         0.0, 0.0, 0.0 )
        self.state_mgr.set_body_velocity( initial_airspeed_mps, 0.0, 0.0 )
        self.phi_rad = 0.0
        self.the_rad = 0.0
        self.psi_rad = 0.0
        self.state_mgr.set_orientation( self.phi_rad,
                                        self.the_rad,
                                        self.psi_rad )
        self.ned2body = quaternion.eul2quat( self.phi_rad,
                                             self.the_rad,
                                             self.psi_rad )
        self.p = 0.0
        self.q = 0.0
        self.r = 0.0
        self.state_mgr.set_gyros( self.p, self.q, self.r )
        self.ax = 0.0
        self.ay = 0.0
        self.az = 0.0
        self.bvx = 0.0
        self.bvy = 0.0
        self.bvz = 0.0
        self.state_mgr.set_accels( self.ax, self.ay, self.az )
        self.time = 0.0
        self.state_mgr.set_time( self.time )
        #self.last_vel_body = None
        self.data = []

    def trim_error(self, xk):
        self.state_mgr.set_throttle( xk[0] )
        self.state_mgr.set_flight_surfaces(xk[1], xk[2], xk[3])
        self.state_mgr.set_orientation(xk[4], xk[5], 0)
        self.state_mgr.alpha = xk[5]
        self.state_mgr.set_airdata(self.trim_airspeed_mps)
        self.state_mgr.set_wind(0.0, 0.0)
        self.state_mgr.set_gyros(0.0, 0.0, 0.0)
        self.state_mgr.set_ned_velocity(self.trim_airspeed_mps, 0.0, 0.0,
                                        0.0, 0.0, 0.0)
        self.state_mgr.compute_body_frame_values(compute_body_vel=False)
        state = self.state_mgr.gen_state_vector(self.params)
        next = self.A @ state
        current = self.state_mgr.state2dict(state)
        result = self.state_mgr.state2dict(next)
        print(result)
        errors = []
        next_asi = result["airspeed"]
        if next_asi < 0: next_asi = 0
        errors.append(self.trim_airspeed_mps - next_asi)
        #errors.append(result["lift"] - result["bgz"])
        errors.append(result["thrust"] - result["drag"])
        #errors.append(result["bax"])
        errors.append(result["bay"])
        #errors.append(result["baz"])
        errors.append(result["p"])
        errors.append(result["q"])
        errors.append(result["r"])
        print(errors)
        return errors
    
    def trim(self, airspeed_mps):
        self.trim_airspeed_mps = airspeed_mps
        initial = [0.5, 0.0, 0.0, 0.0, 0.0, 0.08]
        res = least_squares(self.trim_error, initial, verbose=2)
        print("res:", res)
        print("throttle:", res["x"][0])
        print("aileron:", res["x"][1])
        print("elevator:", res["x"][2])
        print("rudder:", res["x"][3])
        print("phi:", res["x"][3])
        print("theta:", res["x"][4])

    def add_noise(self, next):
        #print(self.trand)
        for i in range(len(next)):
            if "noise" in self.params[i]:
                sum = 0
                if self.trand is None:
                    self.trand = np.random.rand(len(self.params[i]["noise"])) * 2 * pi
                for j, pt in enumerate(self.params[i]["noise"]):
                    sum += sin(self.trand[j]+self.time*2*pi*pt[0]) * pt[1]
                    #print(i, sum)
                next[i] += sum
                    
        
    def update(self):
        state = self.state_mgr.gen_state_vector(self.params)
        #print(self.state2dict(state))

        next = self.A @ state
        self.add_noise(next)
        
        input = self.state_mgr.state2dict(state)
        result = self.state_mgr.dep2dict(next)
        #print("state:", state)
        #print("next:", result)
        #print()

        if False:
            # debug
            field = "bvy"
            idx_list = self.state_mgr.get_state_index( [field] )
            row = self.A[idx_list[0],:]
            print(field, "= ", end="")
            e = []
            for j in range(len(row)):
                e.append(state[j]*row[j])
            idx = np.argsort(-np.abs(e))
            for j in idx:
                print("%.3f (%s) " % (e[j], self.state_mgr.state_list[j]), end="")
            print(" = ", next[idx_list[0]])

        if "airspeed" in result:
            self.airspeed_mps = result["airspeed"]
        else:
            self.airspeed_mps = np.linalg.norm( [result["bvx"],
                                                 result["bvy"],
                                                 result["bvz"]] )
        self.state_mgr.set_airdata(self.airspeed_mps)
        qbar = self.state_mgr.qbar

        if "bax" in result and "airspeed" in result:
            # update body frame velocity from accel estimates (* dt)
            self.bvx += result["bax"] * self.dt
            self.bvy += result["bay"] * self.dt
            self.bvz += result["baz"] * self.dt
            # force bvx to be positive and non-zero (no tail slides here)
            if self.bvx < 0.1:
                self.bvx = 0.1
            # try to clamp alpha/beta from getting crazy
            if abs(self.bvy / self.bvx) > 0.1:
                self.bvy = np.sign(self.bvy) * abs(self.bvx) * 0.1
            if abs(self.bvz / self.bvx) > 0.1:
                self.bvz = np.sign(self.bvz) * abs(self.bvx) * 0.1
            # scale to airspeed
            v = np.array( [self.bvx, self.bvy, self.bvz] )
            v *= (self.airspeed_mps / np.linalg.norm(v))
            self.bvx = v[0]
            self.bvy = v[1]
            self.bvz = v[2]
            self.state_mgr.set_body_velocity( v[0], v[1], v[2] )
            self.alpha = atan2( self.bvz, self.bvx )
            self.beta = atan2( -self.bvy, self.bvx )
        elif "bvx" in result:
            self.bvx = result["bvx"]
            self.bvy = result["bvy"]
            self.bvz = result["bvz"]
            self.state_mgr.set_body_velocity( self.bvx, self.bvy, self.bvz )
            self.alpha = atan2( self.bvz, self.bvx )
            self.beta = atan2( -self.bvy, self.bvx )
        elif "sin(alpha)" in result:
            s_alpha = result["sin(alpha)"] / qbar
            s_beta = result["sin(beta)"] / qbar
            
            # protect against our linear state transtion going out of
            # domain bounds
            if s_alpha > 1: s_alpha = 1
            if s_alpha < -1: s_alpha = -1
            if s_beta > 1: s_beta = 1
            if s_beta < -1: s_beta = -1
            #print(s_alpha, s_beta)
            self.state_mgr.alpha = asin(s_alpha)
            self.state_mgr.beta = asin(s_beta)
        
            # protect against alpha/beta exceeding plausible
            # thresholds for normal flight conditions
            max_angle = 25 * d2r
            if self.state_mgr.alpha > max_angle:
                self.state_mgr.alpha = max_angle
            if self.state_mgr.alpha < -max_angle:
                self.state_mgr.alpha = -max_angle
            if self.state_mgr.beta > max_angle:
                self.state_mgr.beta = max_angle
            if self.state_mgr.beta < -max_angle:
                self.state_mgr.beta = -max_angle
            self.bvx = cos(self.state_mgr.alpha) * self.airspeed_mps
            self.bvy = sin(self.state_mgr.beta) * self.airspeed_mps
            self.bvz = sin(self.state_mgr.alpha) * self.airspeed_mps
            self.state_mgr.set_body_velocity( self.bvx, self.bvy, self.bvz )
        
        self.p = result["p"]
        self.q = result["q"]
        self.r = result["r"]
        self.state_mgr.set_gyros(self.p, self.q, self.r)
        if "ax" in result:
            self.ax = result["ax"]
            self.ay = result["ay"]
            self.az = result["az"]
            self.state_mgr.set_accels(self.ax, self.ay, self.az)

        # update attitude
        rot_body = quaternion.eul2quat(self.p * self.dt,
                                       self.q * self.dt,
                                       self.r * self.dt)
        self.ned2body = quaternion.multiply(self.ned2body, rot_body)
        self.phi_rad, self.the_rad, self.psi_rad = quaternion.quat2eul(self.ned2body)
        self.state_mgr.set_orientation(self.phi_rad, self.the_rad, self.psi_rad)

        self.state_mgr.compute_body_frame_values(compute_body_vel=False)
        
        # velocity in ned frame
        self.vel_ned = quaternion.backTransform( self.ned2body,
                                                 np.array([self.bvx, self.bvy, self.bvz]) )
        self.state_mgr.set_ned_velocity( self.vel_ned[0],
                                         self.vel_ned[1],
                                         self.vel_ned[2],
                                         0.0, 0.0, 0.0 )

        # update position
        self.pos_ned += self.vel_ned * self.dt

        # store data point
        self.data.append(
            [ self.time, self.airspeed_mps,
              self.state_mgr.throttle,
              self.state_mgr.aileron,
              self.state_mgr.elevator,
              self.state_mgr.rudder,
              self.phi_rad, self.the_rad, self.psi_rad,
              self.state_mgr.alpha, self.state_mgr.beta,
              self.p, self.q, self.r] )
        self.data[-1].extend( self.pos_ned.tolist() )
        self.data[-1].extend( self.vel_ned.tolist() )

        # update time
        self.time += self.dt

    def plot(self):
        self.data = np.array(self.data)
        plt.figure()
        plt.plot( self.data[:,0], self.data[:,1], label="Airspeed (mps)" )
        plt.legend()
        plt.figure() 
        plt.plot( self.data[:,0], self.data[:,6]*r2d, label="Roll (deg)" )
        plt.plot( self.data[:,0], self.data[:,7]*r2d, label="Pitch (deg)" )
        plt.legend()
        plt.figure()
        plt.plot( self.data[:,0], self.data[:,9]*r2d, label="self.alpha (deg)" )
        plt.plot( self.data[:,0], self.data[:,10]*r2d, label="self.beta (deg)" )
        plt.legend()
        # plt.figure()
        # plt.plot( self.data[:,0], self.data[:,11], label="body ax (mps^2)" )
        # plt.plot( self.data[:,0], self.data[:,12], label="body ay (mps^2)" )
        # plt.plot( self.data[:,0], self.data[:,13], label="body az (mps^2)" )
        # plt.legend()
        plt.figure()
        plt.plot( self.data[:,0], self.data[:,11]*r2d, label="Roll rate (deg/sec)" )
        plt.plot( self.data[:,0], self.data[:,12]*r2d, label="Pitch rate (deg/sec)" )
        plt.plot( self.data[:,0], self.data[:,13]*r2d, label="Yaw rate (deg/sec)" )
        plt.legend()
        plt.figure()
        plt.plot( self.data[:,0], self.data[:,19], label="Pos 'down' (m)" )
        plt.legend()
        plt.show()
