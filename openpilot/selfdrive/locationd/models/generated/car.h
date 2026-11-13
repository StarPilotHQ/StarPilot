#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_err_fun(double *nom_x, double *delta_x, double *out_2171972427265756634);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6634914969348904448);
void car_H_mod_fun(double *state, double *out_2620521796582229957);
void car_f_fun(double *state, double dt, double *out_9021954438602515933);
void car_F_fun(double *state, double dt, double *out_1280221251351814112);
void car_h_25(double *state, double *unused, double *out_6213416065106593514);
void car_H_25(double *state, double *unused, double *out_902458853407261720);
void car_h_24(double *state, double *unused, double *out_622747324465739350);
void car_H_24(double *state, double *unused, double *out_7556664311515447477);
void car_h_30(double *state, double *unused, double *out_8595878480348935752);
void car_H_30(double *state, double *unused, double *out_3625237476720346478);
void car_h_26(double *state, double *unused, double *out_5559092428904603067);
void car_H_26(double *state, double *unused, double *out_2839044465466794504);
void car_h_27(double *state, double *unused, double *out_3437018544336960962);
void car_H_27(double *state, double *unused, double *out_1401643405536403261);
void car_h_29(double *state, double *unused, double *out_3594205212688090107);
void car_H_29(double *state, double *unused, double *out_3115006132405954294);
void car_h_28(double *state, double *unused, double *out_1629398974381381761);
void car_H_28(double *state, double *unused, double *out_1151375860840628043);
void car_h_31(double *state, double *unused, double *out_978133047748918282);
void car_H_31(double *state, double *unused, double *out_933104815284222148);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}