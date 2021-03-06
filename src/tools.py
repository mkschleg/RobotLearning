"""
Various functions and objects that we found useful to place here 
so all files can share its functionality.

Authors:
    Michele Albach, Parash Rahman, Niko Yasui.
"""

from __future__ import division
from functools import wraps
import math

from cv_bridge.core import CvBridge
import kobuki_msgs.msg as kob_msg
import nav_msgs.msg as nav_msg
import rospy
import sensor_msgs.msg as sens_msg
import sensor_msgs.point_cloud2 as pc2
from turtlesim.msg import Pose
import numpy as np

from time import time

""" topics related to what ROS message type they return
"""
topic_format = {
    "/camera/depth/image": sens_msg.Image,
    "/camera/depth/points": sens_msg.PointCloud2,
    "/camera/ir/image": sens_msg.Image,
    "/camera/rgb/image_raw": sens_msg.Image,
    "/camera/rgb/image_rect_color": sens_msg.Image,
    "/mobile_base/sensors/core": kob_msg.SensorState,
    "/mobile_base/sensors/dock_ir": kob_msg.DockInfraRed,
    "/mobile_base/sensors/imu_data": sens_msg.Imu,
    "/turtle1/pose": Pose,
    "/camera/rgb/image_rect_color/compressed": sens_msg.CompressedImage,
    "/odom": nav_msg.Odometry,
    }

""" features mapped to their corresponding ROS topic
"""
features = {'core': "/mobile_base/sensors/core",
            'ir': "/mobile_base/sensors/dock_ir",
            'imu': "/mobile_base/sensors/imu_data",
            'image': "/camera/rgb/image_rect_color",
            'cimage': "/camera/rgb/image_rect_color/compressed",
            'odom': "/odom",
            'bias': None,
            'bump': None,
            'last_action': None,
            'pixel_pairs': "/camera/rgb/image_rect_color",
            }

def decay(base):
    """Yields the base value divided by t each timestep.

    Used for decaying parameters (e.g. decaying alpha)
    """
    t = 1.0
    while True:
        yield base / t
        t += 1

def constant(base):
    """Yields a constant value.

    Used for constant parameters (e.g. constant alpha)
    """
    while True:
        yield base

def softmax(q):
    max_q = np.max(q)
    exp_q = np.exp(q - max_q)
    return exp_q / exp_q.sum()


def equal_twists(t1, t2):
    """ Determines if two Twist actions are equal

    Args:
        t1, t2: the Twist actions in question
    """
    return all([np.isclose(t1.linear.x, t2.linear.x),
                np.isclose(t1.linear.y, t2.linear.y),
                np.isclose(t1.linear.z, t2.linear.z),
                np.isclose(t1.angular.x, t2.angular.x),
                np.isclose(t1.angular.y, t2.angular.y),
                np.isclose(t1.angular.z, t2.angular.z)])


def action_state_rep(action_space):
    """ Creates a function that generates phi (feature rep of state)
    based on all other aspects of state (e.g. camera, ir, etc.) and 
    action. 

    Args: 
        action_space: a list of all possible actions
    """
    def action_state_phi(state, action):
        phi = np.zeros(state.size * len(action_space))

        for i, current_action in enumerate(action_space):
            if equal_twists(action, current_action):
                phi[np.arange(i * state.size, (i + 1) * state.size)] = state

        return phi

    return action_state_phi


def timing(f):
    """Decorator that print how long a function takes to execute.
    """
    @wraps(f)
    def wrap(*args, **kw):
        ts = time()
        result = f(*args, **kw)
        te = time()
        # print 'func:%r args:[%r, %r] took: %2.4f sec' % \
        # (f.__name__, args, kw, te-ts)
        rospy.logdebug('func:%r took: %2.4f sec' % (f.__name__, te-ts))
        return result
    return wrap


def overrides(interface_class):
    """Decorator that ensures overriden methods are valid.

    Args:
        interface_class: the respective super class

    Example::

            class ConcreteImplementer(MySuperInterface):
                @overrides(MySuperInterface)
                def my_method(self):
                    print 'hello kitty!'
    """
    def overrider(method):
        assert(method.__name__ in dir(interface_class))
        return method
    return overrider

"""
Parsers for easy (but maybe heavy) state creation
"""


def image_parse(img, enc='passthrough'):
    """ Converts a ros image to a numpy array
    """
    br = CvBridge()
    image = np.asarray(br.imgmsg_to_cv2(img, desired_encoding=enc)) 
    return image


def pc2_parse(data):
    """ PointCloud2 parser
    pc2.read_points returns a generator of (x,y,z) tuples
    """
    gen = pc2.read_points(data, skip_nans=True, field_names=("x","y","z"))
    return list(gen)


def sensor_state_parse(data):
    """ Formats all the sensorimotor data into an appropriate 
    dictionary with appropriate types
    """
    return {
    "bump_right": True if data.bumper % 2 else False,
    "bump_center": True if data.bumper % 4 > 1 else False,
    "bump_left": True if data.bumper >= 4 else False,
    "wheeldrop_right": bool(data.WHEEL_DROP_RIGHT),
    "wheeldrop_left": bool(data.WHEEL_DROP_LEFT),
    "cliff_right": bool(data.CLIFF_RIGHT),
    "cliff_center": bool(data.CLIFF_CENTRE),
    "cliff_left": bool(data.CLIFF_LEFT),
    "ticks_left": data.left_encoder,   # number of wheel ticks since 
    "ticks_right": data.right_encoder, # kobuki turned on; max 65535
    # button number
    "button": int(math.log(data.buttons, 2)) if data.buttons else 0, 
    "current_left_mA": data.current[0] * 10,
    "current_right_mA": data.current[1] * 10,
    "overcurrent_left": True if data.over_current % 2 else False,
    "overcurrent_right": True if data.over_current > 2 else False,
    "battery_voltage": data.battery * 0.1,
    "bottom_dist_left": data.bottom[2],  # cliff PSD sensor (0 - 4095, 
    "bottom_dist_right": data.bottom[0], # distance measure is non-linear)
    "bottom_dist_center": data.bottom[1],
    }


def dock_ir_parse(dock_ir):
    return {
    "ir_near_left": bool(dock_ir.NEAR_LEFT),
    "ir_near_center": bool(dock_ir.NEAR_CENTER),
    "ir_near_right": bool(dock_ir.NEAR_RIGHT),
    "ir_far_left": bool(dock_ir.FAR_LEFT),
    "ir_far_center": bool(dock_ir.FAR_CENTER),
    "ir_far_right": bool(dock_ir.FAR_RIGHT),
    }


def imu_parse(data):
    covar = [data.orientation_covariance,
             data.angular_velocity_covariance,
             data.linear_acceleration_covariance]
    covar = [np.asarray(cov).reshape(3,3).tolist() for cov in covar]

    return {
    "orient_x": data.orientation.x,
    "orient_y": data.orientation.y,
    "orient_z": data.orientation.z,
    "orient_w": data.orientation.w,
    "orient_covar": covar[0],
    "ang_vel_x": data.angular_velocity.x,
    "ang_vel_y": data.angular_velocity.y,
    "ang_vel_z": data.angular_velocity.z,
    "ang_vel_covar": covar[1],
    "lin_accel_x": data.linear_acceleration.x,
    "lin_accel_y": data.linear_acceleration.y,
    "lin_accel_z": data.linear_acceleration.z,
    "lin_accel_covar": covar[2],
    }


def get_next_pow2(number):
    """ Gets the next highest power of two for number
    """
    return 2**int(math.ceil(math.log(number, 2)))
