#pragma once
#include "rednose/helpers/common_ekf.h"
extern "C" {
void live_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_9(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_12(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_35(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_32(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_33(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_H(double *in_vec, double *out_6098820432140574500);
void live_err_fun(double *nom_x, double *delta_x, double *out_4601634777746232731);
void live_inv_err_fun(double *nom_x, double *true_x, double *out_2164011828340702218);
void live_H_mod_fun(double *state, double *out_7015072859180032403);
void live_f_fun(double *state, double dt, double *out_5262866806977047040);
void live_F_fun(double *state, double dt, double *out_3307189770372178147);
void live_h_4(double *state, double *unused, double *out_3466064076171392479);
void live_H_4(double *state, double *unused, double *out_3127810815517879899);
void live_h_9(double *state, double *unused, double *out_8851415430512377173);
void live_H_9(double *state, double *unused, double *out_2886621168888289254);
void live_h_10(double *state, double *unused, double *out_6345005294715149579);
void live_H_10(double *state, double *unused, double *out_6395070488296842579);
void live_h_12(double *state, double *unused, double *out_3895333368456862926);
void live_H_12(double *state, double *unused, double *out_1891645592514081896);
void live_h_35(double *state, double *unused, double *out_8916426263363515962);
void live_H_35(double *state, double *unused, double *out_238851241854727477);
void live_h_32(double *state, double *unused, double *out_5644104248396255134);
void live_H_32(double *state, double *unused, double *out_4102153533878379411);
void live_h_13(double *state, double *unused, double *out_6400419631222940909);
void live_H_13(double *state, double *unused, double *out_5136038454533734638);
void live_h_14(double *state, double *unused, double *out_8851415430512377173);
void live_H_14(double *state, double *unused, double *out_2886621168888289254);
void live_h_33(double *state, double *unused, double *out_3148196720499366739);
void live_H_33(double *state, double *unused, double *out_3389408246493585081);
void live_predict(double *in_x, double *in_P, double *in_Q, double dt);
}