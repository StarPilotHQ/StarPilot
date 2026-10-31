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
void err_fun(double *nom_x, double *delta_x, double *out_7865313754094985036) {
   out_7865313754094985036[0] = delta_x[0] + nom_x[0];
   out_7865313754094985036[1] = delta_x[1] + nom_x[1];
   out_7865313754094985036[2] = delta_x[2] + nom_x[2];
   out_7865313754094985036[3] = delta_x[3] + nom_x[3];
   out_7865313754094985036[4] = delta_x[4] + nom_x[4];
   out_7865313754094985036[5] = delta_x[5] + nom_x[5];
   out_7865313754094985036[6] = delta_x[6] + nom_x[6];
   out_7865313754094985036[7] = delta_x[7] + nom_x[7];
   out_7865313754094985036[8] = delta_x[8] + nom_x[8];
   out_7865313754094985036[9] = delta_x[9] + nom_x[9];
   out_7865313754094985036[10] = delta_x[10] + nom_x[10];
   out_7865313754094985036[11] = delta_x[11] + nom_x[11];
   out_7865313754094985036[12] = delta_x[12] + nom_x[12];
   out_7865313754094985036[13] = delta_x[13] + nom_x[13];
   out_7865313754094985036[14] = delta_x[14] + nom_x[14];
   out_7865313754094985036[15] = delta_x[15] + nom_x[15];
   out_7865313754094985036[16] = delta_x[16] + nom_x[16];
   out_7865313754094985036[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_9215330386866768578) {
   out_9215330386866768578[0] = -nom_x[0] + true_x[0];
   out_9215330386866768578[1] = -nom_x[1] + true_x[1];
   out_9215330386866768578[2] = -nom_x[2] + true_x[2];
   out_9215330386866768578[3] = -nom_x[3] + true_x[3];
   out_9215330386866768578[4] = -nom_x[4] + true_x[4];
   out_9215330386866768578[5] = -nom_x[5] + true_x[5];
   out_9215330386866768578[6] = -nom_x[6] + true_x[6];
   out_9215330386866768578[7] = -nom_x[7] + true_x[7];
   out_9215330386866768578[8] = -nom_x[8] + true_x[8];
   out_9215330386866768578[9] = -nom_x[9] + true_x[9];
   out_9215330386866768578[10] = -nom_x[10] + true_x[10];
   out_9215330386866768578[11] = -nom_x[11] + true_x[11];
   out_9215330386866768578[12] = -nom_x[12] + true_x[12];
   out_9215330386866768578[13] = -nom_x[13] + true_x[13];
   out_9215330386866768578[14] = -nom_x[14] + true_x[14];
   out_9215330386866768578[15] = -nom_x[15] + true_x[15];
   out_9215330386866768578[16] = -nom_x[16] + true_x[16];
   out_9215330386866768578[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_1168964832405445386) {
   out_1168964832405445386[0] = 1.0;
   out_1168964832405445386[1] = 0.0;
   out_1168964832405445386[2] = 0.0;
   out_1168964832405445386[3] = 0.0;
   out_1168964832405445386[4] = 0.0;
   out_1168964832405445386[5] = 0.0;
   out_1168964832405445386[6] = 0.0;
   out_1168964832405445386[7] = 0.0;
   out_1168964832405445386[8] = 0.0;
   out_1168964832405445386[9] = 0.0;
   out_1168964832405445386[10] = 0.0;
   out_1168964832405445386[11] = 0.0;
   out_1168964832405445386[12] = 0.0;
   out_1168964832405445386[13] = 0.0;
   out_1168964832405445386[14] = 0.0;
   out_1168964832405445386[15] = 0.0;
   out_1168964832405445386[16] = 0.0;
   out_1168964832405445386[17] = 0.0;
   out_1168964832405445386[18] = 0.0;
   out_1168964832405445386[19] = 1.0;
   out_1168964832405445386[20] = 0.0;
   out_1168964832405445386[21] = 0.0;
   out_1168964832405445386[22] = 0.0;
   out_1168964832405445386[23] = 0.0;
   out_1168964832405445386[24] = 0.0;
   out_1168964832405445386[25] = 0.0;
   out_1168964832405445386[26] = 0.0;
   out_1168964832405445386[27] = 0.0;
   out_1168964832405445386[28] = 0.0;
   out_1168964832405445386[29] = 0.0;
   out_1168964832405445386[30] = 0.0;
   out_1168964832405445386[31] = 0.0;
   out_1168964832405445386[32] = 0.0;
   out_1168964832405445386[33] = 0.0;
   out_1168964832405445386[34] = 0.0;
   out_1168964832405445386[35] = 0.0;
   out_1168964832405445386[36] = 0.0;
   out_1168964832405445386[37] = 0.0;
   out_1168964832405445386[38] = 1.0;
   out_1168964832405445386[39] = 0.0;
   out_1168964832405445386[40] = 0.0;
   out_1168964832405445386[41] = 0.0;
   out_1168964832405445386[42] = 0.0;
   out_1168964832405445386[43] = 0.0;
   out_1168964832405445386[44] = 0.0;
   out_1168964832405445386[45] = 0.0;
   out_1168964832405445386[46] = 0.0;
   out_1168964832405445386[47] = 0.0;
   out_1168964832405445386[48] = 0.0;
   out_1168964832405445386[49] = 0.0;
   out_1168964832405445386[50] = 0.0;
   out_1168964832405445386[51] = 0.0;
   out_1168964832405445386[52] = 0.0;
   out_1168964832405445386[53] = 0.0;
   out_1168964832405445386[54] = 0.0;
   out_1168964832405445386[55] = 0.0;
   out_1168964832405445386[56] = 0.0;
   out_1168964832405445386[57] = 1.0;
   out_1168964832405445386[58] = 0.0;
   out_1168964832405445386[59] = 0.0;
   out_1168964832405445386[60] = 0.0;
   out_1168964832405445386[61] = 0.0;
   out_1168964832405445386[62] = 0.0;
   out_1168964832405445386[63] = 0.0;
   out_1168964832405445386[64] = 0.0;
   out_1168964832405445386[65] = 0.0;
   out_1168964832405445386[66] = 0.0;
   out_1168964832405445386[67] = 0.0;
   out_1168964832405445386[68] = 0.0;
   out_1168964832405445386[69] = 0.0;
   out_1168964832405445386[70] = 0.0;
   out_1168964832405445386[71] = 0.0;
   out_1168964832405445386[72] = 0.0;
   out_1168964832405445386[73] = 0.0;
   out_1168964832405445386[74] = 0.0;
   out_1168964832405445386[75] = 0.0;
   out_1168964832405445386[76] = 1.0;
   out_1168964832405445386[77] = 0.0;
   out_1168964832405445386[78] = 0.0;
   out_1168964832405445386[79] = 0.0;
   out_1168964832405445386[80] = 0.0;
   out_1168964832405445386[81] = 0.0;
   out_1168964832405445386[82] = 0.0;
   out_1168964832405445386[83] = 0.0;
   out_1168964832405445386[84] = 0.0;
   out_1168964832405445386[85] = 0.0;
   out_1168964832405445386[86] = 0.0;
   out_1168964832405445386[87] = 0.0;
   out_1168964832405445386[88] = 0.0;
   out_1168964832405445386[89] = 0.0;
   out_1168964832405445386[90] = 0.0;
   out_1168964832405445386[91] = 0.0;
   out_1168964832405445386[92] = 0.0;
   out_1168964832405445386[93] = 0.0;
   out_1168964832405445386[94] = 0.0;
   out_1168964832405445386[95] = 1.0;
   out_1168964832405445386[96] = 0.0;
   out_1168964832405445386[97] = 0.0;
   out_1168964832405445386[98] = 0.0;
   out_1168964832405445386[99] = 0.0;
   out_1168964832405445386[100] = 0.0;
   out_1168964832405445386[101] = 0.0;
   out_1168964832405445386[102] = 0.0;
   out_1168964832405445386[103] = 0.0;
   out_1168964832405445386[104] = 0.0;
   out_1168964832405445386[105] = 0.0;
   out_1168964832405445386[106] = 0.0;
   out_1168964832405445386[107] = 0.0;
   out_1168964832405445386[108] = 0.0;
   out_1168964832405445386[109] = 0.0;
   out_1168964832405445386[110] = 0.0;
   out_1168964832405445386[111] = 0.0;
   out_1168964832405445386[112] = 0.0;
   out_1168964832405445386[113] = 0.0;
   out_1168964832405445386[114] = 1.0;
   out_1168964832405445386[115] = 0.0;
   out_1168964832405445386[116] = 0.0;
   out_1168964832405445386[117] = 0.0;
   out_1168964832405445386[118] = 0.0;
   out_1168964832405445386[119] = 0.0;
   out_1168964832405445386[120] = 0.0;
   out_1168964832405445386[121] = 0.0;
   out_1168964832405445386[122] = 0.0;
   out_1168964832405445386[123] = 0.0;
   out_1168964832405445386[124] = 0.0;
   out_1168964832405445386[125] = 0.0;
   out_1168964832405445386[126] = 0.0;
   out_1168964832405445386[127] = 0.0;
   out_1168964832405445386[128] = 0.0;
   out_1168964832405445386[129] = 0.0;
   out_1168964832405445386[130] = 0.0;
   out_1168964832405445386[131] = 0.0;
   out_1168964832405445386[132] = 0.0;
   out_1168964832405445386[133] = 1.0;
   out_1168964832405445386[134] = 0.0;
   out_1168964832405445386[135] = 0.0;
   out_1168964832405445386[136] = 0.0;
   out_1168964832405445386[137] = 0.0;
   out_1168964832405445386[138] = 0.0;
   out_1168964832405445386[139] = 0.0;
   out_1168964832405445386[140] = 0.0;
   out_1168964832405445386[141] = 0.0;
   out_1168964832405445386[142] = 0.0;
   out_1168964832405445386[143] = 0.0;
   out_1168964832405445386[144] = 0.0;
   out_1168964832405445386[145] = 0.0;
   out_1168964832405445386[146] = 0.0;
   out_1168964832405445386[147] = 0.0;
   out_1168964832405445386[148] = 0.0;
   out_1168964832405445386[149] = 0.0;
   out_1168964832405445386[150] = 0.0;
   out_1168964832405445386[151] = 0.0;
   out_1168964832405445386[152] = 1.0;
   out_1168964832405445386[153] = 0.0;
   out_1168964832405445386[154] = 0.0;
   out_1168964832405445386[155] = 0.0;
   out_1168964832405445386[156] = 0.0;
   out_1168964832405445386[157] = 0.0;
   out_1168964832405445386[158] = 0.0;
   out_1168964832405445386[159] = 0.0;
   out_1168964832405445386[160] = 0.0;
   out_1168964832405445386[161] = 0.0;
   out_1168964832405445386[162] = 0.0;
   out_1168964832405445386[163] = 0.0;
   out_1168964832405445386[164] = 0.0;
   out_1168964832405445386[165] = 0.0;
   out_1168964832405445386[166] = 0.0;
   out_1168964832405445386[167] = 0.0;
   out_1168964832405445386[168] = 0.0;
   out_1168964832405445386[169] = 0.0;
   out_1168964832405445386[170] = 0.0;
   out_1168964832405445386[171] = 1.0;
   out_1168964832405445386[172] = 0.0;
   out_1168964832405445386[173] = 0.0;
   out_1168964832405445386[174] = 0.0;
   out_1168964832405445386[175] = 0.0;
   out_1168964832405445386[176] = 0.0;
   out_1168964832405445386[177] = 0.0;
   out_1168964832405445386[178] = 0.0;
   out_1168964832405445386[179] = 0.0;
   out_1168964832405445386[180] = 0.0;
   out_1168964832405445386[181] = 0.0;
   out_1168964832405445386[182] = 0.0;
   out_1168964832405445386[183] = 0.0;
   out_1168964832405445386[184] = 0.0;
   out_1168964832405445386[185] = 0.0;
   out_1168964832405445386[186] = 0.0;
   out_1168964832405445386[187] = 0.0;
   out_1168964832405445386[188] = 0.0;
   out_1168964832405445386[189] = 0.0;
   out_1168964832405445386[190] = 1.0;
   out_1168964832405445386[191] = 0.0;
   out_1168964832405445386[192] = 0.0;
   out_1168964832405445386[193] = 0.0;
   out_1168964832405445386[194] = 0.0;
   out_1168964832405445386[195] = 0.0;
   out_1168964832405445386[196] = 0.0;
   out_1168964832405445386[197] = 0.0;
   out_1168964832405445386[198] = 0.0;
   out_1168964832405445386[199] = 0.0;
   out_1168964832405445386[200] = 0.0;
   out_1168964832405445386[201] = 0.0;
   out_1168964832405445386[202] = 0.0;
   out_1168964832405445386[203] = 0.0;
   out_1168964832405445386[204] = 0.0;
   out_1168964832405445386[205] = 0.0;
   out_1168964832405445386[206] = 0.0;
   out_1168964832405445386[207] = 0.0;
   out_1168964832405445386[208] = 0.0;
   out_1168964832405445386[209] = 1.0;
   out_1168964832405445386[210] = 0.0;
   out_1168964832405445386[211] = 0.0;
   out_1168964832405445386[212] = 0.0;
   out_1168964832405445386[213] = 0.0;
   out_1168964832405445386[214] = 0.0;
   out_1168964832405445386[215] = 0.0;
   out_1168964832405445386[216] = 0.0;
   out_1168964832405445386[217] = 0.0;
   out_1168964832405445386[218] = 0.0;
   out_1168964832405445386[219] = 0.0;
   out_1168964832405445386[220] = 0.0;
   out_1168964832405445386[221] = 0.0;
   out_1168964832405445386[222] = 0.0;
   out_1168964832405445386[223] = 0.0;
   out_1168964832405445386[224] = 0.0;
   out_1168964832405445386[225] = 0.0;
   out_1168964832405445386[226] = 0.0;
   out_1168964832405445386[227] = 0.0;
   out_1168964832405445386[228] = 1.0;
   out_1168964832405445386[229] = 0.0;
   out_1168964832405445386[230] = 0.0;
   out_1168964832405445386[231] = 0.0;
   out_1168964832405445386[232] = 0.0;
   out_1168964832405445386[233] = 0.0;
   out_1168964832405445386[234] = 0.0;
   out_1168964832405445386[235] = 0.0;
   out_1168964832405445386[236] = 0.0;
   out_1168964832405445386[237] = 0.0;
   out_1168964832405445386[238] = 0.0;
   out_1168964832405445386[239] = 0.0;
   out_1168964832405445386[240] = 0.0;
   out_1168964832405445386[241] = 0.0;
   out_1168964832405445386[242] = 0.0;
   out_1168964832405445386[243] = 0.0;
   out_1168964832405445386[244] = 0.0;
   out_1168964832405445386[245] = 0.0;
   out_1168964832405445386[246] = 0.0;
   out_1168964832405445386[247] = 1.0;
   out_1168964832405445386[248] = 0.0;
   out_1168964832405445386[249] = 0.0;
   out_1168964832405445386[250] = 0.0;
   out_1168964832405445386[251] = 0.0;
   out_1168964832405445386[252] = 0.0;
   out_1168964832405445386[253] = 0.0;
   out_1168964832405445386[254] = 0.0;
   out_1168964832405445386[255] = 0.0;
   out_1168964832405445386[256] = 0.0;
   out_1168964832405445386[257] = 0.0;
   out_1168964832405445386[258] = 0.0;
   out_1168964832405445386[259] = 0.0;
   out_1168964832405445386[260] = 0.0;
   out_1168964832405445386[261] = 0.0;
   out_1168964832405445386[262] = 0.0;
   out_1168964832405445386[263] = 0.0;
   out_1168964832405445386[264] = 0.0;
   out_1168964832405445386[265] = 0.0;
   out_1168964832405445386[266] = 1.0;
   out_1168964832405445386[267] = 0.0;
   out_1168964832405445386[268] = 0.0;
   out_1168964832405445386[269] = 0.0;
   out_1168964832405445386[270] = 0.0;
   out_1168964832405445386[271] = 0.0;
   out_1168964832405445386[272] = 0.0;
   out_1168964832405445386[273] = 0.0;
   out_1168964832405445386[274] = 0.0;
   out_1168964832405445386[275] = 0.0;
   out_1168964832405445386[276] = 0.0;
   out_1168964832405445386[277] = 0.0;
   out_1168964832405445386[278] = 0.0;
   out_1168964832405445386[279] = 0.0;
   out_1168964832405445386[280] = 0.0;
   out_1168964832405445386[281] = 0.0;
   out_1168964832405445386[282] = 0.0;
   out_1168964832405445386[283] = 0.0;
   out_1168964832405445386[284] = 0.0;
   out_1168964832405445386[285] = 1.0;
   out_1168964832405445386[286] = 0.0;
   out_1168964832405445386[287] = 0.0;
   out_1168964832405445386[288] = 0.0;
   out_1168964832405445386[289] = 0.0;
   out_1168964832405445386[290] = 0.0;
   out_1168964832405445386[291] = 0.0;
   out_1168964832405445386[292] = 0.0;
   out_1168964832405445386[293] = 0.0;
   out_1168964832405445386[294] = 0.0;
   out_1168964832405445386[295] = 0.0;
   out_1168964832405445386[296] = 0.0;
   out_1168964832405445386[297] = 0.0;
   out_1168964832405445386[298] = 0.0;
   out_1168964832405445386[299] = 0.0;
   out_1168964832405445386[300] = 0.0;
   out_1168964832405445386[301] = 0.0;
   out_1168964832405445386[302] = 0.0;
   out_1168964832405445386[303] = 0.0;
   out_1168964832405445386[304] = 1.0;
   out_1168964832405445386[305] = 0.0;
   out_1168964832405445386[306] = 0.0;
   out_1168964832405445386[307] = 0.0;
   out_1168964832405445386[308] = 0.0;
   out_1168964832405445386[309] = 0.0;
   out_1168964832405445386[310] = 0.0;
   out_1168964832405445386[311] = 0.0;
   out_1168964832405445386[312] = 0.0;
   out_1168964832405445386[313] = 0.0;
   out_1168964832405445386[314] = 0.0;
   out_1168964832405445386[315] = 0.0;
   out_1168964832405445386[316] = 0.0;
   out_1168964832405445386[317] = 0.0;
   out_1168964832405445386[318] = 0.0;
   out_1168964832405445386[319] = 0.0;
   out_1168964832405445386[320] = 0.0;
   out_1168964832405445386[321] = 0.0;
   out_1168964832405445386[322] = 0.0;
   out_1168964832405445386[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_1695785533815148768) {
   out_1695785533815148768[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_1695785533815148768[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_1695785533815148768[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_1695785533815148768[3] = dt*state[12] + state[3];
   out_1695785533815148768[4] = dt*state[13] + state[4];
   out_1695785533815148768[5] = dt*state[14] + state[5];
   out_1695785533815148768[6] = state[6];
   out_1695785533815148768[7] = state[7];
   out_1695785533815148768[8] = state[8];
   out_1695785533815148768[9] = state[9];
   out_1695785533815148768[10] = state[10];
   out_1695785533815148768[11] = state[11];
   out_1695785533815148768[12] = state[12];
   out_1695785533815148768[13] = state[13];
   out_1695785533815148768[14] = state[14];
   out_1695785533815148768[15] = state[15];
   out_1695785533815148768[16] = state[16];
   out_1695785533815148768[17] = state[17];
}
void F_fun(double *state, double dt, double *out_9012344706101798543) {
   out_9012344706101798543[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9012344706101798543[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9012344706101798543[2] = 0;
   out_9012344706101798543[3] = 0;
   out_9012344706101798543[4] = 0;
   out_9012344706101798543[5] = 0;
   out_9012344706101798543[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9012344706101798543[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9012344706101798543[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9012344706101798543[9] = 0;
   out_9012344706101798543[10] = 0;
   out_9012344706101798543[11] = 0;
   out_9012344706101798543[12] = 0;
   out_9012344706101798543[13] = 0;
   out_9012344706101798543[14] = 0;
   out_9012344706101798543[15] = 0;
   out_9012344706101798543[16] = 0;
   out_9012344706101798543[17] = 0;
   out_9012344706101798543[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9012344706101798543[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9012344706101798543[20] = 0;
   out_9012344706101798543[21] = 0;
   out_9012344706101798543[22] = 0;
   out_9012344706101798543[23] = 0;
   out_9012344706101798543[24] = 0;
   out_9012344706101798543[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9012344706101798543[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9012344706101798543[27] = 0;
   out_9012344706101798543[28] = 0;
   out_9012344706101798543[29] = 0;
   out_9012344706101798543[30] = 0;
   out_9012344706101798543[31] = 0;
   out_9012344706101798543[32] = 0;
   out_9012344706101798543[33] = 0;
   out_9012344706101798543[34] = 0;
   out_9012344706101798543[35] = 0;
   out_9012344706101798543[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9012344706101798543[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9012344706101798543[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9012344706101798543[39] = 0;
   out_9012344706101798543[40] = 0;
   out_9012344706101798543[41] = 0;
   out_9012344706101798543[42] = 0;
   out_9012344706101798543[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9012344706101798543[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9012344706101798543[45] = 0;
   out_9012344706101798543[46] = 0;
   out_9012344706101798543[47] = 0;
   out_9012344706101798543[48] = 0;
   out_9012344706101798543[49] = 0;
   out_9012344706101798543[50] = 0;
   out_9012344706101798543[51] = 0;
   out_9012344706101798543[52] = 0;
   out_9012344706101798543[53] = 0;
   out_9012344706101798543[54] = 0;
   out_9012344706101798543[55] = 0;
   out_9012344706101798543[56] = 0;
   out_9012344706101798543[57] = 1;
   out_9012344706101798543[58] = 0;
   out_9012344706101798543[59] = 0;
   out_9012344706101798543[60] = 0;
   out_9012344706101798543[61] = 0;
   out_9012344706101798543[62] = 0;
   out_9012344706101798543[63] = 0;
   out_9012344706101798543[64] = 0;
   out_9012344706101798543[65] = 0;
   out_9012344706101798543[66] = dt;
   out_9012344706101798543[67] = 0;
   out_9012344706101798543[68] = 0;
   out_9012344706101798543[69] = 0;
   out_9012344706101798543[70] = 0;
   out_9012344706101798543[71] = 0;
   out_9012344706101798543[72] = 0;
   out_9012344706101798543[73] = 0;
   out_9012344706101798543[74] = 0;
   out_9012344706101798543[75] = 0;
   out_9012344706101798543[76] = 1;
   out_9012344706101798543[77] = 0;
   out_9012344706101798543[78] = 0;
   out_9012344706101798543[79] = 0;
   out_9012344706101798543[80] = 0;
   out_9012344706101798543[81] = 0;
   out_9012344706101798543[82] = 0;
   out_9012344706101798543[83] = 0;
   out_9012344706101798543[84] = 0;
   out_9012344706101798543[85] = dt;
   out_9012344706101798543[86] = 0;
   out_9012344706101798543[87] = 0;
   out_9012344706101798543[88] = 0;
   out_9012344706101798543[89] = 0;
   out_9012344706101798543[90] = 0;
   out_9012344706101798543[91] = 0;
   out_9012344706101798543[92] = 0;
   out_9012344706101798543[93] = 0;
   out_9012344706101798543[94] = 0;
   out_9012344706101798543[95] = 1;
   out_9012344706101798543[96] = 0;
   out_9012344706101798543[97] = 0;
   out_9012344706101798543[98] = 0;
   out_9012344706101798543[99] = 0;
   out_9012344706101798543[100] = 0;
   out_9012344706101798543[101] = 0;
   out_9012344706101798543[102] = 0;
   out_9012344706101798543[103] = 0;
   out_9012344706101798543[104] = dt;
   out_9012344706101798543[105] = 0;
   out_9012344706101798543[106] = 0;
   out_9012344706101798543[107] = 0;
   out_9012344706101798543[108] = 0;
   out_9012344706101798543[109] = 0;
   out_9012344706101798543[110] = 0;
   out_9012344706101798543[111] = 0;
   out_9012344706101798543[112] = 0;
   out_9012344706101798543[113] = 0;
   out_9012344706101798543[114] = 1;
   out_9012344706101798543[115] = 0;
   out_9012344706101798543[116] = 0;
   out_9012344706101798543[117] = 0;
   out_9012344706101798543[118] = 0;
   out_9012344706101798543[119] = 0;
   out_9012344706101798543[120] = 0;
   out_9012344706101798543[121] = 0;
   out_9012344706101798543[122] = 0;
   out_9012344706101798543[123] = 0;
   out_9012344706101798543[124] = 0;
   out_9012344706101798543[125] = 0;
   out_9012344706101798543[126] = 0;
   out_9012344706101798543[127] = 0;
   out_9012344706101798543[128] = 0;
   out_9012344706101798543[129] = 0;
   out_9012344706101798543[130] = 0;
   out_9012344706101798543[131] = 0;
   out_9012344706101798543[132] = 0;
   out_9012344706101798543[133] = 1;
   out_9012344706101798543[134] = 0;
   out_9012344706101798543[135] = 0;
   out_9012344706101798543[136] = 0;
   out_9012344706101798543[137] = 0;
   out_9012344706101798543[138] = 0;
   out_9012344706101798543[139] = 0;
   out_9012344706101798543[140] = 0;
   out_9012344706101798543[141] = 0;
   out_9012344706101798543[142] = 0;
   out_9012344706101798543[143] = 0;
   out_9012344706101798543[144] = 0;
   out_9012344706101798543[145] = 0;
   out_9012344706101798543[146] = 0;
   out_9012344706101798543[147] = 0;
   out_9012344706101798543[148] = 0;
   out_9012344706101798543[149] = 0;
   out_9012344706101798543[150] = 0;
   out_9012344706101798543[151] = 0;
   out_9012344706101798543[152] = 1;
   out_9012344706101798543[153] = 0;
   out_9012344706101798543[154] = 0;
   out_9012344706101798543[155] = 0;
   out_9012344706101798543[156] = 0;
   out_9012344706101798543[157] = 0;
   out_9012344706101798543[158] = 0;
   out_9012344706101798543[159] = 0;
   out_9012344706101798543[160] = 0;
   out_9012344706101798543[161] = 0;
   out_9012344706101798543[162] = 0;
   out_9012344706101798543[163] = 0;
   out_9012344706101798543[164] = 0;
   out_9012344706101798543[165] = 0;
   out_9012344706101798543[166] = 0;
   out_9012344706101798543[167] = 0;
   out_9012344706101798543[168] = 0;
   out_9012344706101798543[169] = 0;
   out_9012344706101798543[170] = 0;
   out_9012344706101798543[171] = 1;
   out_9012344706101798543[172] = 0;
   out_9012344706101798543[173] = 0;
   out_9012344706101798543[174] = 0;
   out_9012344706101798543[175] = 0;
   out_9012344706101798543[176] = 0;
   out_9012344706101798543[177] = 0;
   out_9012344706101798543[178] = 0;
   out_9012344706101798543[179] = 0;
   out_9012344706101798543[180] = 0;
   out_9012344706101798543[181] = 0;
   out_9012344706101798543[182] = 0;
   out_9012344706101798543[183] = 0;
   out_9012344706101798543[184] = 0;
   out_9012344706101798543[185] = 0;
   out_9012344706101798543[186] = 0;
   out_9012344706101798543[187] = 0;
   out_9012344706101798543[188] = 0;
   out_9012344706101798543[189] = 0;
   out_9012344706101798543[190] = 1;
   out_9012344706101798543[191] = 0;
   out_9012344706101798543[192] = 0;
   out_9012344706101798543[193] = 0;
   out_9012344706101798543[194] = 0;
   out_9012344706101798543[195] = 0;
   out_9012344706101798543[196] = 0;
   out_9012344706101798543[197] = 0;
   out_9012344706101798543[198] = 0;
   out_9012344706101798543[199] = 0;
   out_9012344706101798543[200] = 0;
   out_9012344706101798543[201] = 0;
   out_9012344706101798543[202] = 0;
   out_9012344706101798543[203] = 0;
   out_9012344706101798543[204] = 0;
   out_9012344706101798543[205] = 0;
   out_9012344706101798543[206] = 0;
   out_9012344706101798543[207] = 0;
   out_9012344706101798543[208] = 0;
   out_9012344706101798543[209] = 1;
   out_9012344706101798543[210] = 0;
   out_9012344706101798543[211] = 0;
   out_9012344706101798543[212] = 0;
   out_9012344706101798543[213] = 0;
   out_9012344706101798543[214] = 0;
   out_9012344706101798543[215] = 0;
   out_9012344706101798543[216] = 0;
   out_9012344706101798543[217] = 0;
   out_9012344706101798543[218] = 0;
   out_9012344706101798543[219] = 0;
   out_9012344706101798543[220] = 0;
   out_9012344706101798543[221] = 0;
   out_9012344706101798543[222] = 0;
   out_9012344706101798543[223] = 0;
   out_9012344706101798543[224] = 0;
   out_9012344706101798543[225] = 0;
   out_9012344706101798543[226] = 0;
   out_9012344706101798543[227] = 0;
   out_9012344706101798543[228] = 1;
   out_9012344706101798543[229] = 0;
   out_9012344706101798543[230] = 0;
   out_9012344706101798543[231] = 0;
   out_9012344706101798543[232] = 0;
   out_9012344706101798543[233] = 0;
   out_9012344706101798543[234] = 0;
   out_9012344706101798543[235] = 0;
   out_9012344706101798543[236] = 0;
   out_9012344706101798543[237] = 0;
   out_9012344706101798543[238] = 0;
   out_9012344706101798543[239] = 0;
   out_9012344706101798543[240] = 0;
   out_9012344706101798543[241] = 0;
   out_9012344706101798543[242] = 0;
   out_9012344706101798543[243] = 0;
   out_9012344706101798543[244] = 0;
   out_9012344706101798543[245] = 0;
   out_9012344706101798543[246] = 0;
   out_9012344706101798543[247] = 1;
   out_9012344706101798543[248] = 0;
   out_9012344706101798543[249] = 0;
   out_9012344706101798543[250] = 0;
   out_9012344706101798543[251] = 0;
   out_9012344706101798543[252] = 0;
   out_9012344706101798543[253] = 0;
   out_9012344706101798543[254] = 0;
   out_9012344706101798543[255] = 0;
   out_9012344706101798543[256] = 0;
   out_9012344706101798543[257] = 0;
   out_9012344706101798543[258] = 0;
   out_9012344706101798543[259] = 0;
   out_9012344706101798543[260] = 0;
   out_9012344706101798543[261] = 0;
   out_9012344706101798543[262] = 0;
   out_9012344706101798543[263] = 0;
   out_9012344706101798543[264] = 0;
   out_9012344706101798543[265] = 0;
   out_9012344706101798543[266] = 1;
   out_9012344706101798543[267] = 0;
   out_9012344706101798543[268] = 0;
   out_9012344706101798543[269] = 0;
   out_9012344706101798543[270] = 0;
   out_9012344706101798543[271] = 0;
   out_9012344706101798543[272] = 0;
   out_9012344706101798543[273] = 0;
   out_9012344706101798543[274] = 0;
   out_9012344706101798543[275] = 0;
   out_9012344706101798543[276] = 0;
   out_9012344706101798543[277] = 0;
   out_9012344706101798543[278] = 0;
   out_9012344706101798543[279] = 0;
   out_9012344706101798543[280] = 0;
   out_9012344706101798543[281] = 0;
   out_9012344706101798543[282] = 0;
   out_9012344706101798543[283] = 0;
   out_9012344706101798543[284] = 0;
   out_9012344706101798543[285] = 1;
   out_9012344706101798543[286] = 0;
   out_9012344706101798543[287] = 0;
   out_9012344706101798543[288] = 0;
   out_9012344706101798543[289] = 0;
   out_9012344706101798543[290] = 0;
   out_9012344706101798543[291] = 0;
   out_9012344706101798543[292] = 0;
   out_9012344706101798543[293] = 0;
   out_9012344706101798543[294] = 0;
   out_9012344706101798543[295] = 0;
   out_9012344706101798543[296] = 0;
   out_9012344706101798543[297] = 0;
   out_9012344706101798543[298] = 0;
   out_9012344706101798543[299] = 0;
   out_9012344706101798543[300] = 0;
   out_9012344706101798543[301] = 0;
   out_9012344706101798543[302] = 0;
   out_9012344706101798543[303] = 0;
   out_9012344706101798543[304] = 1;
   out_9012344706101798543[305] = 0;
   out_9012344706101798543[306] = 0;
   out_9012344706101798543[307] = 0;
   out_9012344706101798543[308] = 0;
   out_9012344706101798543[309] = 0;
   out_9012344706101798543[310] = 0;
   out_9012344706101798543[311] = 0;
   out_9012344706101798543[312] = 0;
   out_9012344706101798543[313] = 0;
   out_9012344706101798543[314] = 0;
   out_9012344706101798543[315] = 0;
   out_9012344706101798543[316] = 0;
   out_9012344706101798543[317] = 0;
   out_9012344706101798543[318] = 0;
   out_9012344706101798543[319] = 0;
   out_9012344706101798543[320] = 0;
   out_9012344706101798543[321] = 0;
   out_9012344706101798543[322] = 0;
   out_9012344706101798543[323] = 1;
}
void h_4(double *state, double *unused, double *out_6129393296503332397) {
   out_6129393296503332397[0] = state[6] + state[9];
   out_6129393296503332397[1] = state[7] + state[10];
   out_6129393296503332397[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_2031445068696024638) {
   out_2031445068696024638[0] = 0;
   out_2031445068696024638[1] = 0;
   out_2031445068696024638[2] = 0;
   out_2031445068696024638[3] = 0;
   out_2031445068696024638[4] = 0;
   out_2031445068696024638[5] = 0;
   out_2031445068696024638[6] = 1;
   out_2031445068696024638[7] = 0;
   out_2031445068696024638[8] = 0;
   out_2031445068696024638[9] = 1;
   out_2031445068696024638[10] = 0;
   out_2031445068696024638[11] = 0;
   out_2031445068696024638[12] = 0;
   out_2031445068696024638[13] = 0;
   out_2031445068696024638[14] = 0;
   out_2031445068696024638[15] = 0;
   out_2031445068696024638[16] = 0;
   out_2031445068696024638[17] = 0;
   out_2031445068696024638[18] = 0;
   out_2031445068696024638[19] = 0;
   out_2031445068696024638[20] = 0;
   out_2031445068696024638[21] = 0;
   out_2031445068696024638[22] = 0;
   out_2031445068696024638[23] = 0;
   out_2031445068696024638[24] = 0;
   out_2031445068696024638[25] = 1;
   out_2031445068696024638[26] = 0;
   out_2031445068696024638[27] = 0;
   out_2031445068696024638[28] = 1;
   out_2031445068696024638[29] = 0;
   out_2031445068696024638[30] = 0;
   out_2031445068696024638[31] = 0;
   out_2031445068696024638[32] = 0;
   out_2031445068696024638[33] = 0;
   out_2031445068696024638[34] = 0;
   out_2031445068696024638[35] = 0;
   out_2031445068696024638[36] = 0;
   out_2031445068696024638[37] = 0;
   out_2031445068696024638[38] = 0;
   out_2031445068696024638[39] = 0;
   out_2031445068696024638[40] = 0;
   out_2031445068696024638[41] = 0;
   out_2031445068696024638[42] = 0;
   out_2031445068696024638[43] = 0;
   out_2031445068696024638[44] = 1;
   out_2031445068696024638[45] = 0;
   out_2031445068696024638[46] = 0;
   out_2031445068696024638[47] = 1;
   out_2031445068696024638[48] = 0;
   out_2031445068696024638[49] = 0;
   out_2031445068696024638[50] = 0;
   out_2031445068696024638[51] = 0;
   out_2031445068696024638[52] = 0;
   out_2031445068696024638[53] = 0;
}
void h_10(double *state, double *unused, double *out_3045043867218109602) {
   out_3045043867218109602[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_3045043867218109602[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_3045043867218109602[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_2178011878899672658) {
   out_2178011878899672658[0] = 0;
   out_2178011878899672658[1] = 9.8100000000000005*cos(state[1]);
   out_2178011878899672658[2] = 0;
   out_2178011878899672658[3] = 0;
   out_2178011878899672658[4] = -state[8];
   out_2178011878899672658[5] = state[7];
   out_2178011878899672658[6] = 0;
   out_2178011878899672658[7] = state[5];
   out_2178011878899672658[8] = -state[4];
   out_2178011878899672658[9] = 0;
   out_2178011878899672658[10] = 0;
   out_2178011878899672658[11] = 0;
   out_2178011878899672658[12] = 1;
   out_2178011878899672658[13] = 0;
   out_2178011878899672658[14] = 0;
   out_2178011878899672658[15] = 1;
   out_2178011878899672658[16] = 0;
   out_2178011878899672658[17] = 0;
   out_2178011878899672658[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_2178011878899672658[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_2178011878899672658[20] = 0;
   out_2178011878899672658[21] = state[8];
   out_2178011878899672658[22] = 0;
   out_2178011878899672658[23] = -state[6];
   out_2178011878899672658[24] = -state[5];
   out_2178011878899672658[25] = 0;
   out_2178011878899672658[26] = state[3];
   out_2178011878899672658[27] = 0;
   out_2178011878899672658[28] = 0;
   out_2178011878899672658[29] = 0;
   out_2178011878899672658[30] = 0;
   out_2178011878899672658[31] = 1;
   out_2178011878899672658[32] = 0;
   out_2178011878899672658[33] = 0;
   out_2178011878899672658[34] = 1;
   out_2178011878899672658[35] = 0;
   out_2178011878899672658[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_2178011878899672658[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_2178011878899672658[38] = 0;
   out_2178011878899672658[39] = -state[7];
   out_2178011878899672658[40] = state[6];
   out_2178011878899672658[41] = 0;
   out_2178011878899672658[42] = state[4];
   out_2178011878899672658[43] = -state[3];
   out_2178011878899672658[44] = 0;
   out_2178011878899672658[45] = 0;
   out_2178011878899672658[46] = 0;
   out_2178011878899672658[47] = 0;
   out_2178011878899672658[48] = 0;
   out_2178011878899672658[49] = 0;
   out_2178011878899672658[50] = 1;
   out_2178011878899672658[51] = 0;
   out_2178011878899672658[52] = 0;
   out_2178011878899672658[53] = 1;
}
void h_13(double *state, double *unused, double *out_516116815365053521) {
   out_516116815365053521[0] = state[3];
   out_516116815365053521[1] = state[4];
   out_516116815365053521[2] = state[5];
}
void H_13(double *state, double *unused, double *out_5579186139620676291) {
   out_5579186139620676291[0] = 0;
   out_5579186139620676291[1] = 0;
   out_5579186139620676291[2] = 0;
   out_5579186139620676291[3] = 1;
   out_5579186139620676291[4] = 0;
   out_5579186139620676291[5] = 0;
   out_5579186139620676291[6] = 0;
   out_5579186139620676291[7] = 0;
   out_5579186139620676291[8] = 0;
   out_5579186139620676291[9] = 0;
   out_5579186139620676291[10] = 0;
   out_5579186139620676291[11] = 0;
   out_5579186139620676291[12] = 0;
   out_5579186139620676291[13] = 0;
   out_5579186139620676291[14] = 0;
   out_5579186139620676291[15] = 0;
   out_5579186139620676291[16] = 0;
   out_5579186139620676291[17] = 0;
   out_5579186139620676291[18] = 0;
   out_5579186139620676291[19] = 0;
   out_5579186139620676291[20] = 0;
   out_5579186139620676291[21] = 0;
   out_5579186139620676291[22] = 1;
   out_5579186139620676291[23] = 0;
   out_5579186139620676291[24] = 0;
   out_5579186139620676291[25] = 0;
   out_5579186139620676291[26] = 0;
   out_5579186139620676291[27] = 0;
   out_5579186139620676291[28] = 0;
   out_5579186139620676291[29] = 0;
   out_5579186139620676291[30] = 0;
   out_5579186139620676291[31] = 0;
   out_5579186139620676291[32] = 0;
   out_5579186139620676291[33] = 0;
   out_5579186139620676291[34] = 0;
   out_5579186139620676291[35] = 0;
   out_5579186139620676291[36] = 0;
   out_5579186139620676291[37] = 0;
   out_5579186139620676291[38] = 0;
   out_5579186139620676291[39] = 0;
   out_5579186139620676291[40] = 0;
   out_5579186139620676291[41] = 1;
   out_5579186139620676291[42] = 0;
   out_5579186139620676291[43] = 0;
   out_5579186139620676291[44] = 0;
   out_5579186139620676291[45] = 0;
   out_5579186139620676291[46] = 0;
   out_5579186139620676291[47] = 0;
   out_5579186139620676291[48] = 0;
   out_5579186139620676291[49] = 0;
   out_5579186139620676291[50] = 0;
   out_5579186139620676291[51] = 0;
   out_5579186139620676291[52] = 0;
   out_5579186139620676291[53] = 0;
}
void h_14(double *state, double *unused, double *out_327806075788285461) {
   out_327806075788285461[0] = state[6];
   out_327806075788285461[1] = state[7];
   out_327806075788285461[2] = state[8];
}
void H_14(double *state, double *unused, double *out_5114233500991396934) {
   out_5114233500991396934[0] = 0;
   out_5114233500991396934[1] = 0;
   out_5114233500991396934[2] = 0;
   out_5114233500991396934[3] = 0;
   out_5114233500991396934[4] = 0;
   out_5114233500991396934[5] = 0;
   out_5114233500991396934[6] = 1;
   out_5114233500991396934[7] = 0;
   out_5114233500991396934[8] = 0;
   out_5114233500991396934[9] = 0;
   out_5114233500991396934[10] = 0;
   out_5114233500991396934[11] = 0;
   out_5114233500991396934[12] = 0;
   out_5114233500991396934[13] = 0;
   out_5114233500991396934[14] = 0;
   out_5114233500991396934[15] = 0;
   out_5114233500991396934[16] = 0;
   out_5114233500991396934[17] = 0;
   out_5114233500991396934[18] = 0;
   out_5114233500991396934[19] = 0;
   out_5114233500991396934[20] = 0;
   out_5114233500991396934[21] = 0;
   out_5114233500991396934[22] = 0;
   out_5114233500991396934[23] = 0;
   out_5114233500991396934[24] = 0;
   out_5114233500991396934[25] = 1;
   out_5114233500991396934[26] = 0;
   out_5114233500991396934[27] = 0;
   out_5114233500991396934[28] = 0;
   out_5114233500991396934[29] = 0;
   out_5114233500991396934[30] = 0;
   out_5114233500991396934[31] = 0;
   out_5114233500991396934[32] = 0;
   out_5114233500991396934[33] = 0;
   out_5114233500991396934[34] = 0;
   out_5114233500991396934[35] = 0;
   out_5114233500991396934[36] = 0;
   out_5114233500991396934[37] = 0;
   out_5114233500991396934[38] = 0;
   out_5114233500991396934[39] = 0;
   out_5114233500991396934[40] = 0;
   out_5114233500991396934[41] = 0;
   out_5114233500991396934[42] = 0;
   out_5114233500991396934[43] = 0;
   out_5114233500991396934[44] = 1;
   out_5114233500991396934[45] = 0;
   out_5114233500991396934[46] = 0;
   out_5114233500991396934[47] = 0;
   out_5114233500991396934[48] = 0;
   out_5114233500991396934[49] = 0;
   out_5114233500991396934[50] = 0;
   out_5114233500991396934[51] = 0;
   out_5114233500991396934[52] = 0;
   out_5114233500991396934[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_7865313754094985036) {
  err_fun(nom_x, delta_x, out_7865313754094985036);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_9215330386866768578) {
  inv_err_fun(nom_x, true_x, out_9215330386866768578);
}
void pose_H_mod_fun(double *state, double *out_1168964832405445386) {
  H_mod_fun(state, out_1168964832405445386);
}
void pose_f_fun(double *state, double dt, double *out_1695785533815148768) {
  f_fun(state,  dt, out_1695785533815148768);
}
void pose_F_fun(double *state, double dt, double *out_9012344706101798543) {
  F_fun(state,  dt, out_9012344706101798543);
}
void pose_h_4(double *state, double *unused, double *out_6129393296503332397) {
  h_4(state, unused, out_6129393296503332397);
}
void pose_H_4(double *state, double *unused, double *out_2031445068696024638) {
  H_4(state, unused, out_2031445068696024638);
}
void pose_h_10(double *state, double *unused, double *out_3045043867218109602) {
  h_10(state, unused, out_3045043867218109602);
}
void pose_H_10(double *state, double *unused, double *out_2178011878899672658) {
  H_10(state, unused, out_2178011878899672658);
}
void pose_h_13(double *state, double *unused, double *out_516116815365053521) {
  h_13(state, unused, out_516116815365053521);
}
void pose_H_13(double *state, double *unused, double *out_5579186139620676291) {
  H_13(state, unused, out_5579186139620676291);
}
void pose_h_14(double *state, double *unused, double *out_327806075788285461) {
  h_14(state, unused, out_327806075788285461);
}
void pose_H_14(double *state, double *unused, double *out_5114233500991396934) {
  H_14(state, unused, out_5114233500991396934);
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
