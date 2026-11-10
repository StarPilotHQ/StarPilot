#include "pose.h"

namespace {
#define DIM 18
#define EDIM 18
#define MEDIM 18
typedef void (*Hfun)(double *, double *, double *);
const static double MAHA_THRESH_4 = 7.814727903251177;
const static double MAHA_THRESH_10 = 7.814727903251177;
const static double MAHA_THRESH_13 = 7.814727903251177;
const static double MAHA_THRESH_14 = 7.814727903251177;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_2521549048397866319) {
   out_2521549048397866319[0] = delta_x[0] + nom_x[0];
   out_2521549048397866319[1] = delta_x[1] + nom_x[1];
   out_2521549048397866319[2] = delta_x[2] + nom_x[2];
   out_2521549048397866319[3] = delta_x[3] + nom_x[3];
   out_2521549048397866319[4] = delta_x[4] + nom_x[4];
   out_2521549048397866319[5] = delta_x[5] + nom_x[5];
   out_2521549048397866319[6] = delta_x[6] + nom_x[6];
   out_2521549048397866319[7] = delta_x[7] + nom_x[7];
   out_2521549048397866319[8] = delta_x[8] + nom_x[8];
   out_2521549048397866319[9] = delta_x[9] + nom_x[9];
   out_2521549048397866319[10] = delta_x[10] + nom_x[10];
   out_2521549048397866319[11] = delta_x[11] + nom_x[11];
   out_2521549048397866319[12] = delta_x[12] + nom_x[12];
   out_2521549048397866319[13] = delta_x[13] + nom_x[13];
   out_2521549048397866319[14] = delta_x[14] + nom_x[14];
   out_2521549048397866319[15] = delta_x[15] + nom_x[15];
   out_2521549048397866319[16] = delta_x[16] + nom_x[16];
   out_2521549048397866319[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_4368796387448451064) {
   out_4368796387448451064[0] = -nom_x[0] + true_x[0];
   out_4368796387448451064[1] = -nom_x[1] + true_x[1];
   out_4368796387448451064[2] = -nom_x[2] + true_x[2];
   out_4368796387448451064[3] = -nom_x[3] + true_x[3];
   out_4368796387448451064[4] = -nom_x[4] + true_x[4];
   out_4368796387448451064[5] = -nom_x[5] + true_x[5];
   out_4368796387448451064[6] = -nom_x[6] + true_x[6];
   out_4368796387448451064[7] = -nom_x[7] + true_x[7];
   out_4368796387448451064[8] = -nom_x[8] + true_x[8];
   out_4368796387448451064[9] = -nom_x[9] + true_x[9];
   out_4368796387448451064[10] = -nom_x[10] + true_x[10];
   out_4368796387448451064[11] = -nom_x[11] + true_x[11];
   out_4368796387448451064[12] = -nom_x[12] + true_x[12];
   out_4368796387448451064[13] = -nom_x[13] + true_x[13];
   out_4368796387448451064[14] = -nom_x[14] + true_x[14];
   out_4368796387448451064[15] = -nom_x[15] + true_x[15];
   out_4368796387448451064[16] = -nom_x[16] + true_x[16];
   out_4368796387448451064[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_4711938499332200676) {
   out_4711938499332200676[0] = 1.0;
   out_4711938499332200676[1] = 0.0;
   out_4711938499332200676[2] = 0.0;
   out_4711938499332200676[3] = 0.0;
   out_4711938499332200676[4] = 0.0;
   out_4711938499332200676[5] = 0.0;
   out_4711938499332200676[6] = 0.0;
   out_4711938499332200676[7] = 0.0;
   out_4711938499332200676[8] = 0.0;
   out_4711938499332200676[9] = 0.0;
   out_4711938499332200676[10] = 0.0;
   out_4711938499332200676[11] = 0.0;
   out_4711938499332200676[12] = 0.0;
   out_4711938499332200676[13] = 0.0;
   out_4711938499332200676[14] = 0.0;
   out_4711938499332200676[15] = 0.0;
   out_4711938499332200676[16] = 0.0;
   out_4711938499332200676[17] = 0.0;
   out_4711938499332200676[18] = 0.0;
   out_4711938499332200676[19] = 1.0;
   out_4711938499332200676[20] = 0.0;
   out_4711938499332200676[21] = 0.0;
   out_4711938499332200676[22] = 0.0;
   out_4711938499332200676[23] = 0.0;
   out_4711938499332200676[24] = 0.0;
   out_4711938499332200676[25] = 0.0;
   out_4711938499332200676[26] = 0.0;
   out_4711938499332200676[27] = 0.0;
   out_4711938499332200676[28] = 0.0;
   out_4711938499332200676[29] = 0.0;
   out_4711938499332200676[30] = 0.0;
   out_4711938499332200676[31] = 0.0;
   out_4711938499332200676[32] = 0.0;
   out_4711938499332200676[33] = 0.0;
   out_4711938499332200676[34] = 0.0;
   out_4711938499332200676[35] = 0.0;
   out_4711938499332200676[36] = 0.0;
   out_4711938499332200676[37] = 0.0;
   out_4711938499332200676[38] = 1.0;
   out_4711938499332200676[39] = 0.0;
   out_4711938499332200676[40] = 0.0;
   out_4711938499332200676[41] = 0.0;
   out_4711938499332200676[42] = 0.0;
   out_4711938499332200676[43] = 0.0;
   out_4711938499332200676[44] = 0.0;
   out_4711938499332200676[45] = 0.0;
   out_4711938499332200676[46] = 0.0;
   out_4711938499332200676[47] = 0.0;
   out_4711938499332200676[48] = 0.0;
   out_4711938499332200676[49] = 0.0;
   out_4711938499332200676[50] = 0.0;
   out_4711938499332200676[51] = 0.0;
   out_4711938499332200676[52] = 0.0;
   out_4711938499332200676[53] = 0.0;
   out_4711938499332200676[54] = 0.0;
   out_4711938499332200676[55] = 0.0;
   out_4711938499332200676[56] = 0.0;
   out_4711938499332200676[57] = 1.0;
   out_4711938499332200676[58] = 0.0;
   out_4711938499332200676[59] = 0.0;
   out_4711938499332200676[60] = 0.0;
   out_4711938499332200676[61] = 0.0;
   out_4711938499332200676[62] = 0.0;
   out_4711938499332200676[63] = 0.0;
   out_4711938499332200676[64] = 0.0;
   out_4711938499332200676[65] = 0.0;
   out_4711938499332200676[66] = 0.0;
   out_4711938499332200676[67] = 0.0;
   out_4711938499332200676[68] = 0.0;
   out_4711938499332200676[69] = 0.0;
   out_4711938499332200676[70] = 0.0;
   out_4711938499332200676[71] = 0.0;
   out_4711938499332200676[72] = 0.0;
   out_4711938499332200676[73] = 0.0;
   out_4711938499332200676[74] = 0.0;
   out_4711938499332200676[75] = 0.0;
   out_4711938499332200676[76] = 1.0;
   out_4711938499332200676[77] = 0.0;
   out_4711938499332200676[78] = 0.0;
   out_4711938499332200676[79] = 0.0;
   out_4711938499332200676[80] = 0.0;
   out_4711938499332200676[81] = 0.0;
   out_4711938499332200676[82] = 0.0;
   out_4711938499332200676[83] = 0.0;
   out_4711938499332200676[84] = 0.0;
   out_4711938499332200676[85] = 0.0;
   out_4711938499332200676[86] = 0.0;
   out_4711938499332200676[87] = 0.0;
   out_4711938499332200676[88] = 0.0;
   out_4711938499332200676[89] = 0.0;
   out_4711938499332200676[90] = 0.0;
   out_4711938499332200676[91] = 0.0;
   out_4711938499332200676[92] = 0.0;
   out_4711938499332200676[93] = 0.0;
   out_4711938499332200676[94] = 0.0;
   out_4711938499332200676[95] = 1.0;
   out_4711938499332200676[96] = 0.0;
   out_4711938499332200676[97] = 0.0;
   out_4711938499332200676[98] = 0.0;
   out_4711938499332200676[99] = 0.0;
   out_4711938499332200676[100] = 0.0;
   out_4711938499332200676[101] = 0.0;
   out_4711938499332200676[102] = 0.0;
   out_4711938499332200676[103] = 0.0;
   out_4711938499332200676[104] = 0.0;
   out_4711938499332200676[105] = 0.0;
   out_4711938499332200676[106] = 0.0;
   out_4711938499332200676[107] = 0.0;
   out_4711938499332200676[108] = 0.0;
   out_4711938499332200676[109] = 0.0;
   out_4711938499332200676[110] = 0.0;
   out_4711938499332200676[111] = 0.0;
   out_4711938499332200676[112] = 0.0;
   out_4711938499332200676[113] = 0.0;
   out_4711938499332200676[114] = 1.0;
   out_4711938499332200676[115] = 0.0;
   out_4711938499332200676[116] = 0.0;
   out_4711938499332200676[117] = 0.0;
   out_4711938499332200676[118] = 0.0;
   out_4711938499332200676[119] = 0.0;
   out_4711938499332200676[120] = 0.0;
   out_4711938499332200676[121] = 0.0;
   out_4711938499332200676[122] = 0.0;
   out_4711938499332200676[123] = 0.0;
   out_4711938499332200676[124] = 0.0;
   out_4711938499332200676[125] = 0.0;
   out_4711938499332200676[126] = 0.0;
   out_4711938499332200676[127] = 0.0;
   out_4711938499332200676[128] = 0.0;
   out_4711938499332200676[129] = 0.0;
   out_4711938499332200676[130] = 0.0;
   out_4711938499332200676[131] = 0.0;
   out_4711938499332200676[132] = 0.0;
   out_4711938499332200676[133] = 1.0;
   out_4711938499332200676[134] = 0.0;
   out_4711938499332200676[135] = 0.0;
   out_4711938499332200676[136] = 0.0;
   out_4711938499332200676[137] = 0.0;
   out_4711938499332200676[138] = 0.0;
   out_4711938499332200676[139] = 0.0;
   out_4711938499332200676[140] = 0.0;
   out_4711938499332200676[141] = 0.0;
   out_4711938499332200676[142] = 0.0;
   out_4711938499332200676[143] = 0.0;
   out_4711938499332200676[144] = 0.0;
   out_4711938499332200676[145] = 0.0;
   out_4711938499332200676[146] = 0.0;
   out_4711938499332200676[147] = 0.0;
   out_4711938499332200676[148] = 0.0;
   out_4711938499332200676[149] = 0.0;
   out_4711938499332200676[150] = 0.0;
   out_4711938499332200676[151] = 0.0;
   out_4711938499332200676[152] = 1.0;
   out_4711938499332200676[153] = 0.0;
   out_4711938499332200676[154] = 0.0;
   out_4711938499332200676[155] = 0.0;
   out_4711938499332200676[156] = 0.0;
   out_4711938499332200676[157] = 0.0;
   out_4711938499332200676[158] = 0.0;
   out_4711938499332200676[159] = 0.0;
   out_4711938499332200676[160] = 0.0;
   out_4711938499332200676[161] = 0.0;
   out_4711938499332200676[162] = 0.0;
   out_4711938499332200676[163] = 0.0;
   out_4711938499332200676[164] = 0.0;
   out_4711938499332200676[165] = 0.0;
   out_4711938499332200676[166] = 0.0;
   out_4711938499332200676[167] = 0.0;
   out_4711938499332200676[168] = 0.0;
   out_4711938499332200676[169] = 0.0;
   out_4711938499332200676[170] = 0.0;
   out_4711938499332200676[171] = 1.0;
   out_4711938499332200676[172] = 0.0;
   out_4711938499332200676[173] = 0.0;
   out_4711938499332200676[174] = 0.0;
   out_4711938499332200676[175] = 0.0;
   out_4711938499332200676[176] = 0.0;
   out_4711938499332200676[177] = 0.0;
   out_4711938499332200676[178] = 0.0;
   out_4711938499332200676[179] = 0.0;
   out_4711938499332200676[180] = 0.0;
   out_4711938499332200676[181] = 0.0;
   out_4711938499332200676[182] = 0.0;
   out_4711938499332200676[183] = 0.0;
   out_4711938499332200676[184] = 0.0;
   out_4711938499332200676[185] = 0.0;
   out_4711938499332200676[186] = 0.0;
   out_4711938499332200676[187] = 0.0;
   out_4711938499332200676[188] = 0.0;
   out_4711938499332200676[189] = 0.0;
   out_4711938499332200676[190] = 1.0;
   out_4711938499332200676[191] = 0.0;
   out_4711938499332200676[192] = 0.0;
   out_4711938499332200676[193] = 0.0;
   out_4711938499332200676[194] = 0.0;
   out_4711938499332200676[195] = 0.0;
   out_4711938499332200676[196] = 0.0;
   out_4711938499332200676[197] = 0.0;
   out_4711938499332200676[198] = 0.0;
   out_4711938499332200676[199] = 0.0;
   out_4711938499332200676[200] = 0.0;
   out_4711938499332200676[201] = 0.0;
   out_4711938499332200676[202] = 0.0;
   out_4711938499332200676[203] = 0.0;
   out_4711938499332200676[204] = 0.0;
   out_4711938499332200676[205] = 0.0;
   out_4711938499332200676[206] = 0.0;
   out_4711938499332200676[207] = 0.0;
   out_4711938499332200676[208] = 0.0;
   out_4711938499332200676[209] = 1.0;
   out_4711938499332200676[210] = 0.0;
   out_4711938499332200676[211] = 0.0;
   out_4711938499332200676[212] = 0.0;
   out_4711938499332200676[213] = 0.0;
   out_4711938499332200676[214] = 0.0;
   out_4711938499332200676[215] = 0.0;
   out_4711938499332200676[216] = 0.0;
   out_4711938499332200676[217] = 0.0;
   out_4711938499332200676[218] = 0.0;
   out_4711938499332200676[219] = 0.0;
   out_4711938499332200676[220] = 0.0;
   out_4711938499332200676[221] = 0.0;
   out_4711938499332200676[222] = 0.0;
   out_4711938499332200676[223] = 0.0;
   out_4711938499332200676[224] = 0.0;
   out_4711938499332200676[225] = 0.0;
   out_4711938499332200676[226] = 0.0;
   out_4711938499332200676[227] = 0.0;
   out_4711938499332200676[228] = 1.0;
   out_4711938499332200676[229] = 0.0;
   out_4711938499332200676[230] = 0.0;
   out_4711938499332200676[231] = 0.0;
   out_4711938499332200676[232] = 0.0;
   out_4711938499332200676[233] = 0.0;
   out_4711938499332200676[234] = 0.0;
   out_4711938499332200676[235] = 0.0;
   out_4711938499332200676[236] = 0.0;
   out_4711938499332200676[237] = 0.0;
   out_4711938499332200676[238] = 0.0;
   out_4711938499332200676[239] = 0.0;
   out_4711938499332200676[240] = 0.0;
   out_4711938499332200676[241] = 0.0;
   out_4711938499332200676[242] = 0.0;
   out_4711938499332200676[243] = 0.0;
   out_4711938499332200676[244] = 0.0;
   out_4711938499332200676[245] = 0.0;
   out_4711938499332200676[246] = 0.0;
   out_4711938499332200676[247] = 1.0;
   out_4711938499332200676[248] = 0.0;
   out_4711938499332200676[249] = 0.0;
   out_4711938499332200676[250] = 0.0;
   out_4711938499332200676[251] = 0.0;
   out_4711938499332200676[252] = 0.0;
   out_4711938499332200676[253] = 0.0;
   out_4711938499332200676[254] = 0.0;
   out_4711938499332200676[255] = 0.0;
   out_4711938499332200676[256] = 0.0;
   out_4711938499332200676[257] = 0.0;
   out_4711938499332200676[258] = 0.0;
   out_4711938499332200676[259] = 0.0;
   out_4711938499332200676[260] = 0.0;
   out_4711938499332200676[261] = 0.0;
   out_4711938499332200676[262] = 0.0;
   out_4711938499332200676[263] = 0.0;
   out_4711938499332200676[264] = 0.0;
   out_4711938499332200676[265] = 0.0;
   out_4711938499332200676[266] = 1.0;
   out_4711938499332200676[267] = 0.0;
   out_4711938499332200676[268] = 0.0;
   out_4711938499332200676[269] = 0.0;
   out_4711938499332200676[270] = 0.0;
   out_4711938499332200676[271] = 0.0;
   out_4711938499332200676[272] = 0.0;
   out_4711938499332200676[273] = 0.0;
   out_4711938499332200676[274] = 0.0;
   out_4711938499332200676[275] = 0.0;
   out_4711938499332200676[276] = 0.0;
   out_4711938499332200676[277] = 0.0;
   out_4711938499332200676[278] = 0.0;
   out_4711938499332200676[279] = 0.0;
   out_4711938499332200676[280] = 0.0;
   out_4711938499332200676[281] = 0.0;
   out_4711938499332200676[282] = 0.0;
   out_4711938499332200676[283] = 0.0;
   out_4711938499332200676[284] = 0.0;
   out_4711938499332200676[285] = 1.0;
   out_4711938499332200676[286] = 0.0;
   out_4711938499332200676[287] = 0.0;
   out_4711938499332200676[288] = 0.0;
   out_4711938499332200676[289] = 0.0;
   out_4711938499332200676[290] = 0.0;
   out_4711938499332200676[291] = 0.0;
   out_4711938499332200676[292] = 0.0;
   out_4711938499332200676[293] = 0.0;
   out_4711938499332200676[294] = 0.0;
   out_4711938499332200676[295] = 0.0;
   out_4711938499332200676[296] = 0.0;
   out_4711938499332200676[297] = 0.0;
   out_4711938499332200676[298] = 0.0;
   out_4711938499332200676[299] = 0.0;
   out_4711938499332200676[300] = 0.0;
   out_4711938499332200676[301] = 0.0;
   out_4711938499332200676[302] = 0.0;
   out_4711938499332200676[303] = 0.0;
   out_4711938499332200676[304] = 1.0;
   out_4711938499332200676[305] = 0.0;
   out_4711938499332200676[306] = 0.0;
   out_4711938499332200676[307] = 0.0;
   out_4711938499332200676[308] = 0.0;
   out_4711938499332200676[309] = 0.0;
   out_4711938499332200676[310] = 0.0;
   out_4711938499332200676[311] = 0.0;
   out_4711938499332200676[312] = 0.0;
   out_4711938499332200676[313] = 0.0;
   out_4711938499332200676[314] = 0.0;
   out_4711938499332200676[315] = 0.0;
   out_4711938499332200676[316] = 0.0;
   out_4711938499332200676[317] = 0.0;
   out_4711938499332200676[318] = 0.0;
   out_4711938499332200676[319] = 0.0;
   out_4711938499332200676[320] = 0.0;
   out_4711938499332200676[321] = 0.0;
   out_4711938499332200676[322] = 0.0;
   out_4711938499332200676[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_3583623669529018238) {
   out_3583623669529018238[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_3583623669529018238[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_3583623669529018238[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_3583623669529018238[3] = dt*state[12] + state[3];
   out_3583623669529018238[4] = dt*state[13] + state[4];
   out_3583623669529018238[5] = dt*state[14] + state[5];
   out_3583623669529018238[6] = state[6];
   out_3583623669529018238[7] = state[7];
   out_3583623669529018238[8] = state[8];
   out_3583623669529018238[9] = state[9];
   out_3583623669529018238[10] = state[10];
   out_3583623669529018238[11] = state[11];
   out_3583623669529018238[12] = state[12];
   out_3583623669529018238[13] = state[13];
   out_3583623669529018238[14] = state[14];
   out_3583623669529018238[15] = state[15];
   out_3583623669529018238[16] = state[16];
   out_3583623669529018238[17] = state[17];
}
void F_fun(double *state, double dt, double *out_147281941884768427) {
   out_147281941884768427[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_147281941884768427[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_147281941884768427[2] = 0;
   out_147281941884768427[3] = 0;
   out_147281941884768427[4] = 0;
   out_147281941884768427[5] = 0;
   out_147281941884768427[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_147281941884768427[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_147281941884768427[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_147281941884768427[9] = 0;
   out_147281941884768427[10] = 0;
   out_147281941884768427[11] = 0;
   out_147281941884768427[12] = 0;
   out_147281941884768427[13] = 0;
   out_147281941884768427[14] = 0;
   out_147281941884768427[15] = 0;
   out_147281941884768427[16] = 0;
   out_147281941884768427[17] = 0;
   out_147281941884768427[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_147281941884768427[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_147281941884768427[20] = 0;
   out_147281941884768427[21] = 0;
   out_147281941884768427[22] = 0;
   out_147281941884768427[23] = 0;
   out_147281941884768427[24] = 0;
   out_147281941884768427[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_147281941884768427[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_147281941884768427[27] = 0;
   out_147281941884768427[28] = 0;
   out_147281941884768427[29] = 0;
   out_147281941884768427[30] = 0;
   out_147281941884768427[31] = 0;
   out_147281941884768427[32] = 0;
   out_147281941884768427[33] = 0;
   out_147281941884768427[34] = 0;
   out_147281941884768427[35] = 0;
   out_147281941884768427[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_147281941884768427[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_147281941884768427[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_147281941884768427[39] = 0;
   out_147281941884768427[40] = 0;
   out_147281941884768427[41] = 0;
   out_147281941884768427[42] = 0;
   out_147281941884768427[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_147281941884768427[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_147281941884768427[45] = 0;
   out_147281941884768427[46] = 0;
   out_147281941884768427[47] = 0;
   out_147281941884768427[48] = 0;
   out_147281941884768427[49] = 0;
   out_147281941884768427[50] = 0;
   out_147281941884768427[51] = 0;
   out_147281941884768427[52] = 0;
   out_147281941884768427[53] = 0;
   out_147281941884768427[54] = 0;
   out_147281941884768427[55] = 0;
   out_147281941884768427[56] = 0;
   out_147281941884768427[57] = 1;
   out_147281941884768427[58] = 0;
   out_147281941884768427[59] = 0;
   out_147281941884768427[60] = 0;
   out_147281941884768427[61] = 0;
   out_147281941884768427[62] = 0;
   out_147281941884768427[63] = 0;
   out_147281941884768427[64] = 0;
   out_147281941884768427[65] = 0;
   out_147281941884768427[66] = dt;
   out_147281941884768427[67] = 0;
   out_147281941884768427[68] = 0;
   out_147281941884768427[69] = 0;
   out_147281941884768427[70] = 0;
   out_147281941884768427[71] = 0;
   out_147281941884768427[72] = 0;
   out_147281941884768427[73] = 0;
   out_147281941884768427[74] = 0;
   out_147281941884768427[75] = 0;
   out_147281941884768427[76] = 1;
   out_147281941884768427[77] = 0;
   out_147281941884768427[78] = 0;
   out_147281941884768427[79] = 0;
   out_147281941884768427[80] = 0;
   out_147281941884768427[81] = 0;
   out_147281941884768427[82] = 0;
   out_147281941884768427[83] = 0;
   out_147281941884768427[84] = 0;
   out_147281941884768427[85] = dt;
   out_147281941884768427[86] = 0;
   out_147281941884768427[87] = 0;
   out_147281941884768427[88] = 0;
   out_147281941884768427[89] = 0;
   out_147281941884768427[90] = 0;
   out_147281941884768427[91] = 0;
   out_147281941884768427[92] = 0;
   out_147281941884768427[93] = 0;
   out_147281941884768427[94] = 0;
   out_147281941884768427[95] = 1;
   out_147281941884768427[96] = 0;
   out_147281941884768427[97] = 0;
   out_147281941884768427[98] = 0;
   out_147281941884768427[99] = 0;
   out_147281941884768427[100] = 0;
   out_147281941884768427[101] = 0;
   out_147281941884768427[102] = 0;
   out_147281941884768427[103] = 0;
   out_147281941884768427[104] = dt;
   out_147281941884768427[105] = 0;
   out_147281941884768427[106] = 0;
   out_147281941884768427[107] = 0;
   out_147281941884768427[108] = 0;
   out_147281941884768427[109] = 0;
   out_147281941884768427[110] = 0;
   out_147281941884768427[111] = 0;
   out_147281941884768427[112] = 0;
   out_147281941884768427[113] = 0;
   out_147281941884768427[114] = 1;
   out_147281941884768427[115] = 0;
   out_147281941884768427[116] = 0;
   out_147281941884768427[117] = 0;
   out_147281941884768427[118] = 0;
   out_147281941884768427[119] = 0;
   out_147281941884768427[120] = 0;
   out_147281941884768427[121] = 0;
   out_147281941884768427[122] = 0;
   out_147281941884768427[123] = 0;
   out_147281941884768427[124] = 0;
   out_147281941884768427[125] = 0;
   out_147281941884768427[126] = 0;
   out_147281941884768427[127] = 0;
   out_147281941884768427[128] = 0;
   out_147281941884768427[129] = 0;
   out_147281941884768427[130] = 0;
   out_147281941884768427[131] = 0;
   out_147281941884768427[132] = 0;
   out_147281941884768427[133] = 1;
   out_147281941884768427[134] = 0;
   out_147281941884768427[135] = 0;
   out_147281941884768427[136] = 0;
   out_147281941884768427[137] = 0;
   out_147281941884768427[138] = 0;
   out_147281941884768427[139] = 0;
   out_147281941884768427[140] = 0;
   out_147281941884768427[141] = 0;
   out_147281941884768427[142] = 0;
   out_147281941884768427[143] = 0;
   out_147281941884768427[144] = 0;
   out_147281941884768427[145] = 0;
   out_147281941884768427[146] = 0;
   out_147281941884768427[147] = 0;
   out_147281941884768427[148] = 0;
   out_147281941884768427[149] = 0;
   out_147281941884768427[150] = 0;
   out_147281941884768427[151] = 0;
   out_147281941884768427[152] = 1;
   out_147281941884768427[153] = 0;
   out_147281941884768427[154] = 0;
   out_147281941884768427[155] = 0;
   out_147281941884768427[156] = 0;
   out_147281941884768427[157] = 0;
   out_147281941884768427[158] = 0;
   out_147281941884768427[159] = 0;
   out_147281941884768427[160] = 0;
   out_147281941884768427[161] = 0;
   out_147281941884768427[162] = 0;
   out_147281941884768427[163] = 0;
   out_147281941884768427[164] = 0;
   out_147281941884768427[165] = 0;
   out_147281941884768427[166] = 0;
   out_147281941884768427[167] = 0;
   out_147281941884768427[168] = 0;
   out_147281941884768427[169] = 0;
   out_147281941884768427[170] = 0;
   out_147281941884768427[171] = 1;
   out_147281941884768427[172] = 0;
   out_147281941884768427[173] = 0;
   out_147281941884768427[174] = 0;
   out_147281941884768427[175] = 0;
   out_147281941884768427[176] = 0;
   out_147281941884768427[177] = 0;
   out_147281941884768427[178] = 0;
   out_147281941884768427[179] = 0;
   out_147281941884768427[180] = 0;
   out_147281941884768427[181] = 0;
   out_147281941884768427[182] = 0;
   out_147281941884768427[183] = 0;
   out_147281941884768427[184] = 0;
   out_147281941884768427[185] = 0;
   out_147281941884768427[186] = 0;
   out_147281941884768427[187] = 0;
   out_147281941884768427[188] = 0;
   out_147281941884768427[189] = 0;
   out_147281941884768427[190] = 1;
   out_147281941884768427[191] = 0;
   out_147281941884768427[192] = 0;
   out_147281941884768427[193] = 0;
   out_147281941884768427[194] = 0;
   out_147281941884768427[195] = 0;
   out_147281941884768427[196] = 0;
   out_147281941884768427[197] = 0;
   out_147281941884768427[198] = 0;
   out_147281941884768427[199] = 0;
   out_147281941884768427[200] = 0;
   out_147281941884768427[201] = 0;
   out_147281941884768427[202] = 0;
   out_147281941884768427[203] = 0;
   out_147281941884768427[204] = 0;
   out_147281941884768427[205] = 0;
   out_147281941884768427[206] = 0;
   out_147281941884768427[207] = 0;
   out_147281941884768427[208] = 0;
   out_147281941884768427[209] = 1;
   out_147281941884768427[210] = 0;
   out_147281941884768427[211] = 0;
   out_147281941884768427[212] = 0;
   out_147281941884768427[213] = 0;
   out_147281941884768427[214] = 0;
   out_147281941884768427[215] = 0;
   out_147281941884768427[216] = 0;
   out_147281941884768427[217] = 0;
   out_147281941884768427[218] = 0;
   out_147281941884768427[219] = 0;
   out_147281941884768427[220] = 0;
   out_147281941884768427[221] = 0;
   out_147281941884768427[222] = 0;
   out_147281941884768427[223] = 0;
   out_147281941884768427[224] = 0;
   out_147281941884768427[225] = 0;
   out_147281941884768427[226] = 0;
   out_147281941884768427[227] = 0;
   out_147281941884768427[228] = 1;
   out_147281941884768427[229] = 0;
   out_147281941884768427[230] = 0;
   out_147281941884768427[231] = 0;
   out_147281941884768427[232] = 0;
   out_147281941884768427[233] = 0;
   out_147281941884768427[234] = 0;
   out_147281941884768427[235] = 0;
   out_147281941884768427[236] = 0;
   out_147281941884768427[237] = 0;
   out_147281941884768427[238] = 0;
   out_147281941884768427[239] = 0;
   out_147281941884768427[240] = 0;
   out_147281941884768427[241] = 0;
   out_147281941884768427[242] = 0;
   out_147281941884768427[243] = 0;
   out_147281941884768427[244] = 0;
   out_147281941884768427[245] = 0;
   out_147281941884768427[246] = 0;
   out_147281941884768427[247] = 1;
   out_147281941884768427[248] = 0;
   out_147281941884768427[249] = 0;
   out_147281941884768427[250] = 0;
   out_147281941884768427[251] = 0;
   out_147281941884768427[252] = 0;
   out_147281941884768427[253] = 0;
   out_147281941884768427[254] = 0;
   out_147281941884768427[255] = 0;
   out_147281941884768427[256] = 0;
   out_147281941884768427[257] = 0;
   out_147281941884768427[258] = 0;
   out_147281941884768427[259] = 0;
   out_147281941884768427[260] = 0;
   out_147281941884768427[261] = 0;
   out_147281941884768427[262] = 0;
   out_147281941884768427[263] = 0;
   out_147281941884768427[264] = 0;
   out_147281941884768427[265] = 0;
   out_147281941884768427[266] = 1;
   out_147281941884768427[267] = 0;
   out_147281941884768427[268] = 0;
   out_147281941884768427[269] = 0;
   out_147281941884768427[270] = 0;
   out_147281941884768427[271] = 0;
   out_147281941884768427[272] = 0;
   out_147281941884768427[273] = 0;
   out_147281941884768427[274] = 0;
   out_147281941884768427[275] = 0;
   out_147281941884768427[276] = 0;
   out_147281941884768427[277] = 0;
   out_147281941884768427[278] = 0;
   out_147281941884768427[279] = 0;
   out_147281941884768427[280] = 0;
   out_147281941884768427[281] = 0;
   out_147281941884768427[282] = 0;
   out_147281941884768427[283] = 0;
   out_147281941884768427[284] = 0;
   out_147281941884768427[285] = 1;
   out_147281941884768427[286] = 0;
   out_147281941884768427[287] = 0;
   out_147281941884768427[288] = 0;
   out_147281941884768427[289] = 0;
   out_147281941884768427[290] = 0;
   out_147281941884768427[291] = 0;
   out_147281941884768427[292] = 0;
   out_147281941884768427[293] = 0;
   out_147281941884768427[294] = 0;
   out_147281941884768427[295] = 0;
   out_147281941884768427[296] = 0;
   out_147281941884768427[297] = 0;
   out_147281941884768427[298] = 0;
   out_147281941884768427[299] = 0;
   out_147281941884768427[300] = 0;
   out_147281941884768427[301] = 0;
   out_147281941884768427[302] = 0;
   out_147281941884768427[303] = 0;
   out_147281941884768427[304] = 1;
   out_147281941884768427[305] = 0;
   out_147281941884768427[306] = 0;
   out_147281941884768427[307] = 0;
   out_147281941884768427[308] = 0;
   out_147281941884768427[309] = 0;
   out_147281941884768427[310] = 0;
   out_147281941884768427[311] = 0;
   out_147281941884768427[312] = 0;
   out_147281941884768427[313] = 0;
   out_147281941884768427[314] = 0;
   out_147281941884768427[315] = 0;
   out_147281941884768427[316] = 0;
   out_147281941884768427[317] = 0;
   out_147281941884768427[318] = 0;
   out_147281941884768427[319] = 0;
   out_147281941884768427[320] = 0;
   out_147281941884768427[321] = 0;
   out_147281941884768427[322] = 0;
   out_147281941884768427[323] = 1;
}
void h_4(double *state, double *unused, double *out_8338357592566013610) {
   out_8338357592566013610[0] = state[6] + state[9];
   out_8338357592566013610[1] = state[7] + state[10];
   out_8338357592566013610[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_940131289488293104) {
   out_940131289488293104[0] = 0;
   out_940131289488293104[1] = 0;
   out_940131289488293104[2] = 0;
   out_940131289488293104[3] = 0;
   out_940131289488293104[4] = 0;
   out_940131289488293104[5] = 0;
   out_940131289488293104[6] = 1;
   out_940131289488293104[7] = 0;
   out_940131289488293104[8] = 0;
   out_940131289488293104[9] = 1;
   out_940131289488293104[10] = 0;
   out_940131289488293104[11] = 0;
   out_940131289488293104[12] = 0;
   out_940131289488293104[13] = 0;
   out_940131289488293104[14] = 0;
   out_940131289488293104[15] = 0;
   out_940131289488293104[16] = 0;
   out_940131289488293104[17] = 0;
   out_940131289488293104[18] = 0;
   out_940131289488293104[19] = 0;
   out_940131289488293104[20] = 0;
   out_940131289488293104[21] = 0;
   out_940131289488293104[22] = 0;
   out_940131289488293104[23] = 0;
   out_940131289488293104[24] = 0;
   out_940131289488293104[25] = 1;
   out_940131289488293104[26] = 0;
   out_940131289488293104[27] = 0;
   out_940131289488293104[28] = 1;
   out_940131289488293104[29] = 0;
   out_940131289488293104[30] = 0;
   out_940131289488293104[31] = 0;
   out_940131289488293104[32] = 0;
   out_940131289488293104[33] = 0;
   out_940131289488293104[34] = 0;
   out_940131289488293104[35] = 0;
   out_940131289488293104[36] = 0;
   out_940131289488293104[37] = 0;
   out_940131289488293104[38] = 0;
   out_940131289488293104[39] = 0;
   out_940131289488293104[40] = 0;
   out_940131289488293104[41] = 0;
   out_940131289488293104[42] = 0;
   out_940131289488293104[43] = 0;
   out_940131289488293104[44] = 1;
   out_940131289488293104[45] = 0;
   out_940131289488293104[46] = 0;
   out_940131289488293104[47] = 1;
   out_940131289488293104[48] = 0;
   out_940131289488293104[49] = 0;
   out_940131289488293104[50] = 0;
   out_940131289488293104[51] = 0;
   out_940131289488293104[52] = 0;
   out_940131289488293104[53] = 0;
}
void h_10(double *state, double *unused, double *out_8345234452777105931) {
   out_8345234452777105931[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_8345234452777105931[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_8345234452777105931[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_1248319787069001495) {
   out_1248319787069001495[0] = 0;
   out_1248319787069001495[1] = 9.8100000000000005*cos(state[1]);
   out_1248319787069001495[2] = 0;
   out_1248319787069001495[3] = 0;
   out_1248319787069001495[4] = -state[8];
   out_1248319787069001495[5] = state[7];
   out_1248319787069001495[6] = 0;
   out_1248319787069001495[7] = state[5];
   out_1248319787069001495[8] = -state[4];
   out_1248319787069001495[9] = 0;
   out_1248319787069001495[10] = 0;
   out_1248319787069001495[11] = 0;
   out_1248319787069001495[12] = 1;
   out_1248319787069001495[13] = 0;
   out_1248319787069001495[14] = 0;
   out_1248319787069001495[15] = 1;
   out_1248319787069001495[16] = 0;
   out_1248319787069001495[17] = 0;
   out_1248319787069001495[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_1248319787069001495[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_1248319787069001495[20] = 0;
   out_1248319787069001495[21] = state[8];
   out_1248319787069001495[22] = 0;
   out_1248319787069001495[23] = -state[6];
   out_1248319787069001495[24] = -state[5];
   out_1248319787069001495[25] = 0;
   out_1248319787069001495[26] = state[3];
   out_1248319787069001495[27] = 0;
   out_1248319787069001495[28] = 0;
   out_1248319787069001495[29] = 0;
   out_1248319787069001495[30] = 0;
   out_1248319787069001495[31] = 1;
   out_1248319787069001495[32] = 0;
   out_1248319787069001495[33] = 0;
   out_1248319787069001495[34] = 1;
   out_1248319787069001495[35] = 0;
   out_1248319787069001495[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_1248319787069001495[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_1248319787069001495[38] = 0;
   out_1248319787069001495[39] = -state[7];
   out_1248319787069001495[40] = state[6];
   out_1248319787069001495[41] = 0;
   out_1248319787069001495[42] = state[4];
   out_1248319787069001495[43] = -state[3];
   out_1248319787069001495[44] = 0;
   out_1248319787069001495[45] = 0;
   out_1248319787069001495[46] = 0;
   out_1248319787069001495[47] = 0;
   out_1248319787069001495[48] = 0;
   out_1248319787069001495[49] = 0;
   out_1248319787069001495[50] = 1;
   out_1248319787069001495[51] = 0;
   out_1248319787069001495[52] = 0;
   out_1248319787069001495[53] = 1;
}
void h_13(double *state, double *unused, double *out_1047798615184752827) {
   out_1047798615184752827[0] = state[3];
   out_1047798615184752827[1] = state[4];
   out_1047798615184752827[2] = state[5];
}
void H_13(double *state, double *unused, double *out_375529369806449000) {
   out_375529369806449000[0] = 0;
   out_375529369806449000[1] = 0;
   out_375529369806449000[2] = 0;
   out_375529369806449000[3] = 1;
   out_375529369806449000[4] = 0;
   out_375529369806449000[5] = 0;
   out_375529369806449000[6] = 0;
   out_375529369806449000[7] = 0;
   out_375529369806449000[8] = 0;
   out_375529369806449000[9] = 0;
   out_375529369806449000[10] = 0;
   out_375529369806449000[11] = 0;
   out_375529369806449000[12] = 0;
   out_375529369806449000[13] = 0;
   out_375529369806449000[14] = 0;
   out_375529369806449000[15] = 0;
   out_375529369806449000[16] = 0;
   out_375529369806449000[17] = 0;
   out_375529369806449000[18] = 0;
   out_375529369806449000[19] = 0;
   out_375529369806449000[20] = 0;
   out_375529369806449000[21] = 0;
   out_375529369806449000[22] = 1;
   out_375529369806449000[23] = 0;
   out_375529369806449000[24] = 0;
   out_375529369806449000[25] = 0;
   out_375529369806449000[26] = 0;
   out_375529369806449000[27] = 0;
   out_375529369806449000[28] = 0;
   out_375529369806449000[29] = 0;
   out_375529369806449000[30] = 0;
   out_375529369806449000[31] = 0;
   out_375529369806449000[32] = 0;
   out_375529369806449000[33] = 0;
   out_375529369806449000[34] = 0;
   out_375529369806449000[35] = 0;
   out_375529369806449000[36] = 0;
   out_375529369806449000[37] = 0;
   out_375529369806449000[38] = 0;
   out_375529369806449000[39] = 0;
   out_375529369806449000[40] = 0;
   out_375529369806449000[41] = 1;
   out_375529369806449000[42] = 0;
   out_375529369806449000[43] = 0;
   out_375529369806449000[44] = 0;
   out_375529369806449000[45] = 0;
   out_375529369806449000[46] = 0;
   out_375529369806449000[47] = 0;
   out_375529369806449000[48] = 0;
   out_375529369806449000[49] = 0;
   out_375529369806449000[50] = 0;
   out_375529369806449000[51] = 0;
   out_375529369806449000[52] = 0;
   out_375529369806449000[53] = 0;
}
void h_14(double *state, double *unused, double *out_4235679239024063539) {
   out_4235679239024063539[0] = state[6];
   out_4235679239024063539[1] = state[7];
   out_4235679239024063539[2] = state[8];
}
void H_14(double *state, double *unused, double *out_4022919721783665400) {
   out_4022919721783665400[0] = 0;
   out_4022919721783665400[1] = 0;
   out_4022919721783665400[2] = 0;
   out_4022919721783665400[3] = 0;
   out_4022919721783665400[4] = 0;
   out_4022919721783665400[5] = 0;
   out_4022919721783665400[6] = 1;
   out_4022919721783665400[7] = 0;
   out_4022919721783665400[8] = 0;
   out_4022919721783665400[9] = 0;
   out_4022919721783665400[10] = 0;
   out_4022919721783665400[11] = 0;
   out_4022919721783665400[12] = 0;
   out_4022919721783665400[13] = 0;
   out_4022919721783665400[14] = 0;
   out_4022919721783665400[15] = 0;
   out_4022919721783665400[16] = 0;
   out_4022919721783665400[17] = 0;
   out_4022919721783665400[18] = 0;
   out_4022919721783665400[19] = 0;
   out_4022919721783665400[20] = 0;
   out_4022919721783665400[21] = 0;
   out_4022919721783665400[22] = 0;
   out_4022919721783665400[23] = 0;
   out_4022919721783665400[24] = 0;
   out_4022919721783665400[25] = 1;
   out_4022919721783665400[26] = 0;
   out_4022919721783665400[27] = 0;
   out_4022919721783665400[28] = 0;
   out_4022919721783665400[29] = 0;
   out_4022919721783665400[30] = 0;
   out_4022919721783665400[31] = 0;
   out_4022919721783665400[32] = 0;
   out_4022919721783665400[33] = 0;
   out_4022919721783665400[34] = 0;
   out_4022919721783665400[35] = 0;
   out_4022919721783665400[36] = 0;
   out_4022919721783665400[37] = 0;
   out_4022919721783665400[38] = 0;
   out_4022919721783665400[39] = 0;
   out_4022919721783665400[40] = 0;
   out_4022919721783665400[41] = 0;
   out_4022919721783665400[42] = 0;
   out_4022919721783665400[43] = 0;
   out_4022919721783665400[44] = 1;
   out_4022919721783665400[45] = 0;
   out_4022919721783665400[46] = 0;
   out_4022919721783665400[47] = 0;
   out_4022919721783665400[48] = 0;
   out_4022919721783665400[49] = 0;
   out_4022919721783665400[50] = 0;
   out_4022919721783665400[51] = 0;
   out_4022919721783665400[52] = 0;
   out_4022919721783665400[53] = 0;
}
#include <eigen3/Eigen/Dense>
#include <iostream>

typedef Eigen::Matrix<double, DIM, DIM, Eigen::RowMajor> DDM;
typedef Eigen::Matrix<double, EDIM, EDIM, Eigen::RowMajor> EEM;
typedef Eigen::Matrix<double, DIM, EDIM, Eigen::RowMajor> DEM;

void predict(double *in_x, double *in_P, double *in_Q, double dt) {
  typedef Eigen::Matrix<double, MEDIM, MEDIM, Eigen::RowMajor> RRM;

  double nx[DIM] = {0};
  double in_F[EDIM*EDIM] = {0};

  // functions from sympy
  f_fun(in_x, dt, nx);
  F_fun(in_x, dt, in_F);


  EEM F(in_F);
  EEM P(in_P);
  EEM Q(in_Q);

  RRM F_main = F.topLeftCorner(MEDIM, MEDIM);
  P.topLeftCorner(MEDIM, MEDIM) = (F_main * P.topLeftCorner(MEDIM, MEDIM)) * F_main.transpose();
  P.topRightCorner(MEDIM, EDIM - MEDIM) = F_main * P.topRightCorner(MEDIM, EDIM - MEDIM);
  P.bottomLeftCorner(EDIM - MEDIM, MEDIM) = P.bottomLeftCorner(EDIM - MEDIM, MEDIM) * F_main.transpose();

  P = P + dt*Q;

  // copy out state
  memcpy(in_x, nx, DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
}

// note: extra_args dim only correct when null space projecting
// otherwise 1
template <int ZDIM, int EADIM, bool MAHA_TEST>
void update(double *in_x, double *in_P, Hfun h_fun, Hfun H_fun, Hfun Hea_fun, double *in_z, double *in_R, double *in_ea, double MAHA_THRESHOLD) {
  typedef Eigen::Matrix<double, ZDIM, ZDIM, Eigen::RowMajor> ZZM;
  typedef Eigen::Matrix<double, ZDIM, DIM, Eigen::RowMajor> ZDM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, EDIM, Eigen::RowMajor> XEM;
  //typedef Eigen::Matrix<double, EDIM, ZDIM, Eigen::RowMajor> EZM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, 1> X1M;
  typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor> XXM;

  double in_hx[ZDIM] = {0};
  double in_H[ZDIM * DIM] = {0};
  double in_H_mod[EDIM * DIM] = {0};
  double delta_x[EDIM] = {0};
  double x_new[DIM] = {0};


  // state x, P
  Eigen::Matrix<double, ZDIM, 1> z(in_z);
  EEM P(in_P);
  ZZM pre_R(in_R);

  // functions from sympy
  h_fun(in_x, in_ea, in_hx);
  H_fun(in_x, in_ea, in_H);
  ZDM pre_H(in_H);

  // get y (y = z - hx)
  Eigen::Matrix<double, ZDIM, 1> pre_y(in_hx); pre_y = z - pre_y;
  X1M y; XXM H; XXM R;
  if (Hea_fun){
    typedef Eigen::Matrix<double, ZDIM, EADIM, Eigen::RowMajor> ZAM;
    double in_Hea[ZDIM * EADIM] = {0};
    Hea_fun(in_x, in_ea, in_Hea);
    ZAM Hea(in_Hea);
    XXM A = Hea.transpose().fullPivLu().kernel();


    y = A.transpose() * pre_y;
    H = A.transpose() * pre_H;
    R = A.transpose() * pre_R * A;
  } else {
    y = pre_y;
    H = pre_H;
    R = pre_R;
  }
  // get modified H
  H_mod_fun(in_x, in_H_mod);
  DEM H_mod(in_H_mod);
  XEM H_err = H * H_mod;

  // Do mahalobis distance test
  if (MAHA_TEST){
    XXM a = (H_err * P * H_err.transpose() + R).inverse();
    double maha_dist = y.transpose() * a * y;
    if (maha_dist > MAHA_THRESHOLD){
      R = 1.0e16 * R;
    }
  }

  // Outlier resilient weighting
  double weight = 1;//(1.5)/(1 + y.squaredNorm()/R.sum());

  // kalman gains and I_KH
  XXM S = ((H_err * P) * H_err.transpose()) + R/weight;
  XEM KT = S.fullPivLu().solve(H_err * P.transpose());
  //EZM K = KT.transpose(); TODO: WHY DOES THIS NOT COMPILE?
  //EZM K = S.fullPivLu().solve(H_err * P.transpose()).transpose();
  //std::cout << "Here is the matrix rot:\n" << K << std::endl;
  EEM I_KH = Eigen::Matrix<double, EDIM, EDIM>::Identity() - (KT.transpose() * H_err);

  // update state by injecting dx
  Eigen::Matrix<double, EDIM, 1> dx(delta_x);
  dx  = (KT.transpose() * y);
  memcpy(delta_x, dx.data(), EDIM * sizeof(double));
  err_fun(in_x, delta_x, x_new);
  Eigen::Matrix<double, DIM, 1> x(x_new);

  // update cov
  P = ((I_KH * P) * I_KH.transpose()) + ((KT.transpose() * R) * KT);

  // copy out state
  memcpy(in_x, x.data(), DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
  memcpy(in_z, y.data(), y.rows() * sizeof(double));
}




}
extern "C" {

void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_4, H_4, NULL, in_z, in_R, in_ea, MAHA_THRESH_4);
}
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_10, H_10, NULL, in_z, in_R, in_ea, MAHA_THRESH_10);
}
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_13, H_13, NULL, in_z, in_R, in_ea, MAHA_THRESH_13);
}
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_14, H_14, NULL, in_z, in_R, in_ea, MAHA_THRESH_14);
}
void pose_err_fun(double *nom_x, double *delta_x, double *out_2521549048397866319) {
  err_fun(nom_x, delta_x, out_2521549048397866319);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_4368796387448451064) {
  inv_err_fun(nom_x, true_x, out_4368796387448451064);
}
void pose_H_mod_fun(double *state, double *out_4711938499332200676) {
  H_mod_fun(state, out_4711938499332200676);
}
void pose_f_fun(double *state, double dt, double *out_3583623669529018238) {
  f_fun(state,  dt, out_3583623669529018238);
}
void pose_F_fun(double *state, double dt, double *out_147281941884768427) {
  F_fun(state,  dt, out_147281941884768427);
}
void pose_h_4(double *state, double *unused, double *out_8338357592566013610) {
  h_4(state, unused, out_8338357592566013610);
}
void pose_H_4(double *state, double *unused, double *out_940131289488293104) {
  H_4(state, unused, out_940131289488293104);
}
void pose_h_10(double *state, double *unused, double *out_8345234452777105931) {
  h_10(state, unused, out_8345234452777105931);
}
void pose_H_10(double *state, double *unused, double *out_1248319787069001495) {
  H_10(state, unused, out_1248319787069001495);
}
void pose_h_13(double *state, double *unused, double *out_1047798615184752827) {
  h_13(state, unused, out_1047798615184752827);
}
void pose_H_13(double *state, double *unused, double *out_375529369806449000) {
  H_13(state, unused, out_375529369806449000);
}
void pose_h_14(double *state, double *unused, double *out_4235679239024063539) {
  h_14(state, unused, out_4235679239024063539);
}
void pose_H_14(double *state, double *unused, double *out_4022919721783665400) {
  H_14(state, unused, out_4022919721783665400);
}
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
}

const EKF pose = {
  .name = "pose",
  .kinds = { 4, 10, 13, 14 },
  .feature_kinds = {  },
  .f_fun = pose_f_fun,
  .F_fun = pose_F_fun,
  .err_fun = pose_err_fun,
  .inv_err_fun = pose_inv_err_fun,
  .H_mod_fun = pose_H_mod_fun,
  .predict = pose_predict,
  .hs = {
    { 4, pose_h_4 },
    { 10, pose_h_10 },
    { 13, pose_h_13 },
    { 14, pose_h_14 },
  },
  .Hs = {
    { 4, pose_H_4 },
    { 10, pose_H_10 },
    { 13, pose_H_13 },
    { 14, pose_H_14 },
  },
  .updates = {
    { 4, pose_update_4 },
    { 10, pose_update_10 },
    { 13, pose_update_13 },
    { 14, pose_update_14 },
  },
  .Hes = {
  },
  .sets = {
  },
  .extra_routines = {
  },
};

ekf_lib_init(pose)
