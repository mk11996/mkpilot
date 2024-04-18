#pragma once
#include "rednose/helpers/common_ekf.h"
extern "C" {
void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_err_fun(double *nom_x, double *delta_x, double *out_7247719309480281051);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_2681782549687900672);
void car_H_mod_fun(double *state, double *out_137576970368068800);
void car_f_fun(double *state, double dt, double *out_7115045003314014650);
void car_F_fun(double *state, double dt, double *out_6431170143172849662);
void car_h_25(double *state, double *unused, double *out_4546935715871708115);
void car_H_25(double *state, double *unused, double *out_6641128993824440257);
void car_h_24(double *state, double *unused, double *out_5145588108859611737);
void car_H_24(double *state, double *unused, double *out_7059105752918602325);
void car_h_30(double *state, double *unused, double *out_7128973131487492565);
void car_H_30(double *state, double *unused, double *out_4122796035317191630);
void car_h_26(double *state, double *unused, double *out_3185015005230737899);
void car_H_26(double *state, double *unused, double *out_8064111761011055135);
void car_h_27(double *state, double *unused, double *out_148983634590079103);
void car_H_27(double *state, double *unused, double *out_1899201964133248413);
void car_h_29(double *state, double *unused, double *out_729931614073643419);
void car_H_29(double *state, double *unused, double *out_3612564691002799446);
void car_h_28(double *state, double *unused, double *out_5953535801143115287);
void car_H_28(double *state, double *unused, double *out_8694963708072330020);
void car_h_31(double *state, double *unused, double *out_3346003779012815244);
void car_H_31(double *state, double *unused, double *out_6610483031947479829);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}