#pragma once
#include "rednose/helpers/common_ekf.h"
extern "C" {
void gnss_update_6(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void gnss_update_20(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void gnss_update_7(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void gnss_update_21(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void gnss_err_fun(double *nom_x, double *delta_x, double *out_3268890645376421587);
void gnss_inv_err_fun(double *nom_x, double *true_x, double *out_7549878915460633121);
void gnss_H_mod_fun(double *state, double *out_8094327699774317216);
void gnss_f_fun(double *state, double dt, double *out_6578835107177820652);
void gnss_F_fun(double *state, double dt, double *out_3329755413866591228);
void gnss_h_6(double *state, double *sat_pos, double *out_6067569867114337350);
void gnss_H_6(double *state, double *sat_pos, double *out_9148125727656389237);
void gnss_h_20(double *state, double *sat_pos, double *out_1046469546504212269);
void gnss_H_20(double *state, double *sat_pos, double *out_4611278801988723742);
void gnss_h_7(double *state, double *sat_pos_vel, double *out_826926030772729278);
void gnss_H_7(double *state, double *sat_pos_vel, double *out_8482751189000492385);
void gnss_h_21(double *state, double *sat_pos_vel, double *out_826926030772729278);
void gnss_H_21(double *state, double *sat_pos_vel, double *out_8482751189000492385);
void gnss_predict(double *in_x, double *in_P, double *in_Q, double dt);
}