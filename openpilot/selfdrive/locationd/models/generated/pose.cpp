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
void err_fun(double *nom_x, double *delta_x, double *out_5912021845748559900) {
   out_5912021845748559900[0] = delta_x[0] + nom_x[0];
   out_5912021845748559900[1] = delta_x[1] + nom_x[1];
   out_5912021845748559900[2] = delta_x[2] + nom_x[2];
   out_5912021845748559900[3] = delta_x[3] + nom_x[3];
   out_5912021845748559900[4] = delta_x[4] + nom_x[4];
   out_5912021845748559900[5] = delta_x[5] + nom_x[5];
   out_5912021845748559900[6] = delta_x[6] + nom_x[6];
   out_5912021845748559900[7] = delta_x[7] + nom_x[7];
   out_5912021845748559900[8] = delta_x[8] + nom_x[8];
   out_5912021845748559900[9] = delta_x[9] + nom_x[9];
   out_5912021845748559900[10] = delta_x[10] + nom_x[10];
   out_5912021845748559900[11] = delta_x[11] + nom_x[11];
   out_5912021845748559900[12] = delta_x[12] + nom_x[12];
   out_5912021845748559900[13] = delta_x[13] + nom_x[13];
   out_5912021845748559900[14] = delta_x[14] + nom_x[14];
   out_5912021845748559900[15] = delta_x[15] + nom_x[15];
   out_5912021845748559900[16] = delta_x[16] + nom_x[16];
   out_5912021845748559900[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_8843896357291937735) {
   out_8843896357291937735[0] = -nom_x[0] + true_x[0];
   out_8843896357291937735[1] = -nom_x[1] + true_x[1];
   out_8843896357291937735[2] = -nom_x[2] + true_x[2];
   out_8843896357291937735[3] = -nom_x[3] + true_x[3];
   out_8843896357291937735[4] = -nom_x[4] + true_x[4];
   out_8843896357291937735[5] = -nom_x[5] + true_x[5];
   out_8843896357291937735[6] = -nom_x[6] + true_x[6];
   out_8843896357291937735[7] = -nom_x[7] + true_x[7];
   out_8843896357291937735[8] = -nom_x[8] + true_x[8];
   out_8843896357291937735[9] = -nom_x[9] + true_x[9];
   out_8843896357291937735[10] = -nom_x[10] + true_x[10];
   out_8843896357291937735[11] = -nom_x[11] + true_x[11];
   out_8843896357291937735[12] = -nom_x[12] + true_x[12];
   out_8843896357291937735[13] = -nom_x[13] + true_x[13];
   out_8843896357291937735[14] = -nom_x[14] + true_x[14];
   out_8843896357291937735[15] = -nom_x[15] + true_x[15];
   out_8843896357291937735[16] = -nom_x[16] + true_x[16];
   out_8843896357291937735[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_2688320196777535589) {
   out_2688320196777535589[0] = 1.0;
   out_2688320196777535589[1] = 0.0;
   out_2688320196777535589[2] = 0.0;
   out_2688320196777535589[3] = 0.0;
   out_2688320196777535589[4] = 0.0;
   out_2688320196777535589[5] = 0.0;
   out_2688320196777535589[6] = 0.0;
   out_2688320196777535589[7] = 0.0;
   out_2688320196777535589[8] = 0.0;
   out_2688320196777535589[9] = 0.0;
   out_2688320196777535589[10] = 0.0;
   out_2688320196777535589[11] = 0.0;
   out_2688320196777535589[12] = 0.0;
   out_2688320196777535589[13] = 0.0;
   out_2688320196777535589[14] = 0.0;
   out_2688320196777535589[15] = 0.0;
   out_2688320196777535589[16] = 0.0;
   out_2688320196777535589[17] = 0.0;
   out_2688320196777535589[18] = 0.0;
   out_2688320196777535589[19] = 1.0;
   out_2688320196777535589[20] = 0.0;
   out_2688320196777535589[21] = 0.0;
   out_2688320196777535589[22] = 0.0;
   out_2688320196777535589[23] = 0.0;
   out_2688320196777535589[24] = 0.0;
   out_2688320196777535589[25] = 0.0;
   out_2688320196777535589[26] = 0.0;
   out_2688320196777535589[27] = 0.0;
   out_2688320196777535589[28] = 0.0;
   out_2688320196777535589[29] = 0.0;
   out_2688320196777535589[30] = 0.0;
   out_2688320196777535589[31] = 0.0;
   out_2688320196777535589[32] = 0.0;
   out_2688320196777535589[33] = 0.0;
   out_2688320196777535589[34] = 0.0;
   out_2688320196777535589[35] = 0.0;
   out_2688320196777535589[36] = 0.0;
   out_2688320196777535589[37] = 0.0;
   out_2688320196777535589[38] = 1.0;
   out_2688320196777535589[39] = 0.0;
   out_2688320196777535589[40] = 0.0;
   out_2688320196777535589[41] = 0.0;
   out_2688320196777535589[42] = 0.0;
   out_2688320196777535589[43] = 0.0;
   out_2688320196777535589[44] = 0.0;
   out_2688320196777535589[45] = 0.0;
   out_2688320196777535589[46] = 0.0;
   out_2688320196777535589[47] = 0.0;
   out_2688320196777535589[48] = 0.0;
   out_2688320196777535589[49] = 0.0;
   out_2688320196777535589[50] = 0.0;
   out_2688320196777535589[51] = 0.0;
   out_2688320196777535589[52] = 0.0;
   out_2688320196777535589[53] = 0.0;
   out_2688320196777535589[54] = 0.0;
   out_2688320196777535589[55] = 0.0;
   out_2688320196777535589[56] = 0.0;
   out_2688320196777535589[57] = 1.0;
   out_2688320196777535589[58] = 0.0;
   out_2688320196777535589[59] = 0.0;
   out_2688320196777535589[60] = 0.0;
   out_2688320196777535589[61] = 0.0;
   out_2688320196777535589[62] = 0.0;
   out_2688320196777535589[63] = 0.0;
   out_2688320196777535589[64] = 0.0;
   out_2688320196777535589[65] = 0.0;
   out_2688320196777535589[66] = 0.0;
   out_2688320196777535589[67] = 0.0;
   out_2688320196777535589[68] = 0.0;
   out_2688320196777535589[69] = 0.0;
   out_2688320196777535589[70] = 0.0;
   out_2688320196777535589[71] = 0.0;
   out_2688320196777535589[72] = 0.0;
   out_2688320196777535589[73] = 0.0;
   out_2688320196777535589[74] = 0.0;
   out_2688320196777535589[75] = 0.0;
   out_2688320196777535589[76] = 1.0;
   out_2688320196777535589[77] = 0.0;
   out_2688320196777535589[78] = 0.0;
   out_2688320196777535589[79] = 0.0;
   out_2688320196777535589[80] = 0.0;
   out_2688320196777535589[81] = 0.0;
   out_2688320196777535589[82] = 0.0;
   out_2688320196777535589[83] = 0.0;
   out_2688320196777535589[84] = 0.0;
   out_2688320196777535589[85] = 0.0;
   out_2688320196777535589[86] = 0.0;
   out_2688320196777535589[87] = 0.0;
   out_2688320196777535589[88] = 0.0;
   out_2688320196777535589[89] = 0.0;
   out_2688320196777535589[90] = 0.0;
   out_2688320196777535589[91] = 0.0;
   out_2688320196777535589[92] = 0.0;
   out_2688320196777535589[93] = 0.0;
   out_2688320196777535589[94] = 0.0;
   out_2688320196777535589[95] = 1.0;
   out_2688320196777535589[96] = 0.0;
   out_2688320196777535589[97] = 0.0;
   out_2688320196777535589[98] = 0.0;
   out_2688320196777535589[99] = 0.0;
   out_2688320196777535589[100] = 0.0;
   out_2688320196777535589[101] = 0.0;
   out_2688320196777535589[102] = 0.0;
   out_2688320196777535589[103] = 0.0;
   out_2688320196777535589[104] = 0.0;
   out_2688320196777535589[105] = 0.0;
   out_2688320196777535589[106] = 0.0;
   out_2688320196777535589[107] = 0.0;
   out_2688320196777535589[108] = 0.0;
   out_2688320196777535589[109] = 0.0;
   out_2688320196777535589[110] = 0.0;
   out_2688320196777535589[111] = 0.0;
   out_2688320196777535589[112] = 0.0;
   out_2688320196777535589[113] = 0.0;
   out_2688320196777535589[114] = 1.0;
   out_2688320196777535589[115] = 0.0;
   out_2688320196777535589[116] = 0.0;
   out_2688320196777535589[117] = 0.0;
   out_2688320196777535589[118] = 0.0;
   out_2688320196777535589[119] = 0.0;
   out_2688320196777535589[120] = 0.0;
   out_2688320196777535589[121] = 0.0;
   out_2688320196777535589[122] = 0.0;
   out_2688320196777535589[123] = 0.0;
   out_2688320196777535589[124] = 0.0;
   out_2688320196777535589[125] = 0.0;
   out_2688320196777535589[126] = 0.0;
   out_2688320196777535589[127] = 0.0;
   out_2688320196777535589[128] = 0.0;
   out_2688320196777535589[129] = 0.0;
   out_2688320196777535589[130] = 0.0;
   out_2688320196777535589[131] = 0.0;
   out_2688320196777535589[132] = 0.0;
   out_2688320196777535589[133] = 1.0;
   out_2688320196777535589[134] = 0.0;
   out_2688320196777535589[135] = 0.0;
   out_2688320196777535589[136] = 0.0;
   out_2688320196777535589[137] = 0.0;
   out_2688320196777535589[138] = 0.0;
   out_2688320196777535589[139] = 0.0;
   out_2688320196777535589[140] = 0.0;
   out_2688320196777535589[141] = 0.0;
   out_2688320196777535589[142] = 0.0;
   out_2688320196777535589[143] = 0.0;
   out_2688320196777535589[144] = 0.0;
   out_2688320196777535589[145] = 0.0;
   out_2688320196777535589[146] = 0.0;
   out_2688320196777535589[147] = 0.0;
   out_2688320196777535589[148] = 0.0;
   out_2688320196777535589[149] = 0.0;
   out_2688320196777535589[150] = 0.0;
   out_2688320196777535589[151] = 0.0;
   out_2688320196777535589[152] = 1.0;
   out_2688320196777535589[153] = 0.0;
   out_2688320196777535589[154] = 0.0;
   out_2688320196777535589[155] = 0.0;
   out_2688320196777535589[156] = 0.0;
   out_2688320196777535589[157] = 0.0;
   out_2688320196777535589[158] = 0.0;
   out_2688320196777535589[159] = 0.0;
   out_2688320196777535589[160] = 0.0;
   out_2688320196777535589[161] = 0.0;
   out_2688320196777535589[162] = 0.0;
   out_2688320196777535589[163] = 0.0;
   out_2688320196777535589[164] = 0.0;
   out_2688320196777535589[165] = 0.0;
   out_2688320196777535589[166] = 0.0;
   out_2688320196777535589[167] = 0.0;
   out_2688320196777535589[168] = 0.0;
   out_2688320196777535589[169] = 0.0;
   out_2688320196777535589[170] = 0.0;
   out_2688320196777535589[171] = 1.0;
   out_2688320196777535589[172] = 0.0;
   out_2688320196777535589[173] = 0.0;
   out_2688320196777535589[174] = 0.0;
   out_2688320196777535589[175] = 0.0;
   out_2688320196777535589[176] = 0.0;
   out_2688320196777535589[177] = 0.0;
   out_2688320196777535589[178] = 0.0;
   out_2688320196777535589[179] = 0.0;
   out_2688320196777535589[180] = 0.0;
   out_2688320196777535589[181] = 0.0;
   out_2688320196777535589[182] = 0.0;
   out_2688320196777535589[183] = 0.0;
   out_2688320196777535589[184] = 0.0;
   out_2688320196777535589[185] = 0.0;
   out_2688320196777535589[186] = 0.0;
   out_2688320196777535589[187] = 0.0;
   out_2688320196777535589[188] = 0.0;
   out_2688320196777535589[189] = 0.0;
   out_2688320196777535589[190] = 1.0;
   out_2688320196777535589[191] = 0.0;
   out_2688320196777535589[192] = 0.0;
   out_2688320196777535589[193] = 0.0;
   out_2688320196777535589[194] = 0.0;
   out_2688320196777535589[195] = 0.0;
   out_2688320196777535589[196] = 0.0;
   out_2688320196777535589[197] = 0.0;
   out_2688320196777535589[198] = 0.0;
   out_2688320196777535589[199] = 0.0;
   out_2688320196777535589[200] = 0.0;
   out_2688320196777535589[201] = 0.0;
   out_2688320196777535589[202] = 0.0;
   out_2688320196777535589[203] = 0.0;
   out_2688320196777535589[204] = 0.0;
   out_2688320196777535589[205] = 0.0;
   out_2688320196777535589[206] = 0.0;
   out_2688320196777535589[207] = 0.0;
   out_2688320196777535589[208] = 0.0;
   out_2688320196777535589[209] = 1.0;
   out_2688320196777535589[210] = 0.0;
   out_2688320196777535589[211] = 0.0;
   out_2688320196777535589[212] = 0.0;
   out_2688320196777535589[213] = 0.0;
   out_2688320196777535589[214] = 0.0;
   out_2688320196777535589[215] = 0.0;
   out_2688320196777535589[216] = 0.0;
   out_2688320196777535589[217] = 0.0;
   out_2688320196777535589[218] = 0.0;
   out_2688320196777535589[219] = 0.0;
   out_2688320196777535589[220] = 0.0;
   out_2688320196777535589[221] = 0.0;
   out_2688320196777535589[222] = 0.0;
   out_2688320196777535589[223] = 0.0;
   out_2688320196777535589[224] = 0.0;
   out_2688320196777535589[225] = 0.0;
   out_2688320196777535589[226] = 0.0;
   out_2688320196777535589[227] = 0.0;
   out_2688320196777535589[228] = 1.0;
   out_2688320196777535589[229] = 0.0;
   out_2688320196777535589[230] = 0.0;
   out_2688320196777535589[231] = 0.0;
   out_2688320196777535589[232] = 0.0;
   out_2688320196777535589[233] = 0.0;
   out_2688320196777535589[234] = 0.0;
   out_2688320196777535589[235] = 0.0;
   out_2688320196777535589[236] = 0.0;
   out_2688320196777535589[237] = 0.0;
   out_2688320196777535589[238] = 0.0;
   out_2688320196777535589[239] = 0.0;
   out_2688320196777535589[240] = 0.0;
   out_2688320196777535589[241] = 0.0;
   out_2688320196777535589[242] = 0.0;
   out_2688320196777535589[243] = 0.0;
   out_2688320196777535589[244] = 0.0;
   out_2688320196777535589[245] = 0.0;
   out_2688320196777535589[246] = 0.0;
   out_2688320196777535589[247] = 1.0;
   out_2688320196777535589[248] = 0.0;
   out_2688320196777535589[249] = 0.0;
   out_2688320196777535589[250] = 0.0;
   out_2688320196777535589[251] = 0.0;
   out_2688320196777535589[252] = 0.0;
   out_2688320196777535589[253] = 0.0;
   out_2688320196777535589[254] = 0.0;
   out_2688320196777535589[255] = 0.0;
   out_2688320196777535589[256] = 0.0;
   out_2688320196777535589[257] = 0.0;
   out_2688320196777535589[258] = 0.0;
   out_2688320196777535589[259] = 0.0;
   out_2688320196777535589[260] = 0.0;
   out_2688320196777535589[261] = 0.0;
   out_2688320196777535589[262] = 0.0;
   out_2688320196777535589[263] = 0.0;
   out_2688320196777535589[264] = 0.0;
   out_2688320196777535589[265] = 0.0;
   out_2688320196777535589[266] = 1.0;
   out_2688320196777535589[267] = 0.0;
   out_2688320196777535589[268] = 0.0;
   out_2688320196777535589[269] = 0.0;
   out_2688320196777535589[270] = 0.0;
   out_2688320196777535589[271] = 0.0;
   out_2688320196777535589[272] = 0.0;
   out_2688320196777535589[273] = 0.0;
   out_2688320196777535589[274] = 0.0;
   out_2688320196777535589[275] = 0.0;
   out_2688320196777535589[276] = 0.0;
   out_2688320196777535589[277] = 0.0;
   out_2688320196777535589[278] = 0.0;
   out_2688320196777535589[279] = 0.0;
   out_2688320196777535589[280] = 0.0;
   out_2688320196777535589[281] = 0.0;
   out_2688320196777535589[282] = 0.0;
   out_2688320196777535589[283] = 0.0;
   out_2688320196777535589[284] = 0.0;
   out_2688320196777535589[285] = 1.0;
   out_2688320196777535589[286] = 0.0;
   out_2688320196777535589[287] = 0.0;
   out_2688320196777535589[288] = 0.0;
   out_2688320196777535589[289] = 0.0;
   out_2688320196777535589[290] = 0.0;
   out_2688320196777535589[291] = 0.0;
   out_2688320196777535589[292] = 0.0;
   out_2688320196777535589[293] = 0.0;
   out_2688320196777535589[294] = 0.0;
   out_2688320196777535589[295] = 0.0;
   out_2688320196777535589[296] = 0.0;
   out_2688320196777535589[297] = 0.0;
   out_2688320196777535589[298] = 0.0;
   out_2688320196777535589[299] = 0.0;
   out_2688320196777535589[300] = 0.0;
   out_2688320196777535589[301] = 0.0;
   out_2688320196777535589[302] = 0.0;
   out_2688320196777535589[303] = 0.0;
   out_2688320196777535589[304] = 1.0;
   out_2688320196777535589[305] = 0.0;
   out_2688320196777535589[306] = 0.0;
   out_2688320196777535589[307] = 0.0;
   out_2688320196777535589[308] = 0.0;
   out_2688320196777535589[309] = 0.0;
   out_2688320196777535589[310] = 0.0;
   out_2688320196777535589[311] = 0.0;
   out_2688320196777535589[312] = 0.0;
   out_2688320196777535589[313] = 0.0;
   out_2688320196777535589[314] = 0.0;
   out_2688320196777535589[315] = 0.0;
   out_2688320196777535589[316] = 0.0;
   out_2688320196777535589[317] = 0.0;
   out_2688320196777535589[318] = 0.0;
   out_2688320196777535589[319] = 0.0;
   out_2688320196777535589[320] = 0.0;
   out_2688320196777535589[321] = 0.0;
   out_2688320196777535589[322] = 0.0;
   out_2688320196777535589[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_650263676124789422) {
   out_650263676124789422[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_650263676124789422[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_650263676124789422[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_650263676124789422[3] = dt*state[12] + state[3];
   out_650263676124789422[4] = dt*state[13] + state[4];
   out_650263676124789422[5] = dt*state[14] + state[5];
   out_650263676124789422[6] = state[6];
   out_650263676124789422[7] = state[7];
   out_650263676124789422[8] = state[8];
   out_650263676124789422[9] = state[9];
   out_650263676124789422[10] = state[10];
   out_650263676124789422[11] = state[11];
   out_650263676124789422[12] = state[12];
   out_650263676124789422[13] = state[13];
   out_650263676124789422[14] = state[14];
   out_650263676124789422[15] = state[15];
   out_650263676124789422[16] = state[16];
   out_650263676124789422[17] = state[17];
}
void F_fun(double *state, double dt, double *out_7315646842237993430) {
   out_7315646842237993430[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_7315646842237993430[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_7315646842237993430[2] = 0;
   out_7315646842237993430[3] = 0;
   out_7315646842237993430[4] = 0;
   out_7315646842237993430[5] = 0;
   out_7315646842237993430[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_7315646842237993430[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_7315646842237993430[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_7315646842237993430[9] = 0;
   out_7315646842237993430[10] = 0;
   out_7315646842237993430[11] = 0;
   out_7315646842237993430[12] = 0;
   out_7315646842237993430[13] = 0;
   out_7315646842237993430[14] = 0;
   out_7315646842237993430[15] = 0;
   out_7315646842237993430[16] = 0;
   out_7315646842237993430[17] = 0;
   out_7315646842237993430[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_7315646842237993430[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_7315646842237993430[20] = 0;
   out_7315646842237993430[21] = 0;
   out_7315646842237993430[22] = 0;
   out_7315646842237993430[23] = 0;
   out_7315646842237993430[24] = 0;
   out_7315646842237993430[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_7315646842237993430[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_7315646842237993430[27] = 0;
   out_7315646842237993430[28] = 0;
   out_7315646842237993430[29] = 0;
   out_7315646842237993430[30] = 0;
   out_7315646842237993430[31] = 0;
   out_7315646842237993430[32] = 0;
   out_7315646842237993430[33] = 0;
   out_7315646842237993430[34] = 0;
   out_7315646842237993430[35] = 0;
   out_7315646842237993430[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_7315646842237993430[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_7315646842237993430[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_7315646842237993430[39] = 0;
   out_7315646842237993430[40] = 0;
   out_7315646842237993430[41] = 0;
   out_7315646842237993430[42] = 0;
   out_7315646842237993430[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_7315646842237993430[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_7315646842237993430[45] = 0;
   out_7315646842237993430[46] = 0;
   out_7315646842237993430[47] = 0;
   out_7315646842237993430[48] = 0;
   out_7315646842237993430[49] = 0;
   out_7315646842237993430[50] = 0;
   out_7315646842237993430[51] = 0;
   out_7315646842237993430[52] = 0;
   out_7315646842237993430[53] = 0;
   out_7315646842237993430[54] = 0;
   out_7315646842237993430[55] = 0;
   out_7315646842237993430[56] = 0;
   out_7315646842237993430[57] = 1;
   out_7315646842237993430[58] = 0;
   out_7315646842237993430[59] = 0;
   out_7315646842237993430[60] = 0;
   out_7315646842237993430[61] = 0;
   out_7315646842237993430[62] = 0;
   out_7315646842237993430[63] = 0;
   out_7315646842237993430[64] = 0;
   out_7315646842237993430[65] = 0;
   out_7315646842237993430[66] = dt;
   out_7315646842237993430[67] = 0;
   out_7315646842237993430[68] = 0;
   out_7315646842237993430[69] = 0;
   out_7315646842237993430[70] = 0;
   out_7315646842237993430[71] = 0;
   out_7315646842237993430[72] = 0;
   out_7315646842237993430[73] = 0;
   out_7315646842237993430[74] = 0;
   out_7315646842237993430[75] = 0;
   out_7315646842237993430[76] = 1;
   out_7315646842237993430[77] = 0;
   out_7315646842237993430[78] = 0;
   out_7315646842237993430[79] = 0;
   out_7315646842237993430[80] = 0;
   out_7315646842237993430[81] = 0;
   out_7315646842237993430[82] = 0;
   out_7315646842237993430[83] = 0;
   out_7315646842237993430[84] = 0;
   out_7315646842237993430[85] = dt;
   out_7315646842237993430[86] = 0;
   out_7315646842237993430[87] = 0;
   out_7315646842237993430[88] = 0;
   out_7315646842237993430[89] = 0;
   out_7315646842237993430[90] = 0;
   out_7315646842237993430[91] = 0;
   out_7315646842237993430[92] = 0;
   out_7315646842237993430[93] = 0;
   out_7315646842237993430[94] = 0;
   out_7315646842237993430[95] = 1;
   out_7315646842237993430[96] = 0;
   out_7315646842237993430[97] = 0;
   out_7315646842237993430[98] = 0;
   out_7315646842237993430[99] = 0;
   out_7315646842237993430[100] = 0;
   out_7315646842237993430[101] = 0;
   out_7315646842237993430[102] = 0;
   out_7315646842237993430[103] = 0;
   out_7315646842237993430[104] = dt;
   out_7315646842237993430[105] = 0;
   out_7315646842237993430[106] = 0;
   out_7315646842237993430[107] = 0;
   out_7315646842237993430[108] = 0;
   out_7315646842237993430[109] = 0;
   out_7315646842237993430[110] = 0;
   out_7315646842237993430[111] = 0;
   out_7315646842237993430[112] = 0;
   out_7315646842237993430[113] = 0;
   out_7315646842237993430[114] = 1;
   out_7315646842237993430[115] = 0;
   out_7315646842237993430[116] = 0;
   out_7315646842237993430[117] = 0;
   out_7315646842237993430[118] = 0;
   out_7315646842237993430[119] = 0;
   out_7315646842237993430[120] = 0;
   out_7315646842237993430[121] = 0;
   out_7315646842237993430[122] = 0;
   out_7315646842237993430[123] = 0;
   out_7315646842237993430[124] = 0;
   out_7315646842237993430[125] = 0;
   out_7315646842237993430[126] = 0;
   out_7315646842237993430[127] = 0;
   out_7315646842237993430[128] = 0;
   out_7315646842237993430[129] = 0;
   out_7315646842237993430[130] = 0;
   out_7315646842237993430[131] = 0;
   out_7315646842237993430[132] = 0;
   out_7315646842237993430[133] = 1;
   out_7315646842237993430[134] = 0;
   out_7315646842237993430[135] = 0;
   out_7315646842237993430[136] = 0;
   out_7315646842237993430[137] = 0;
   out_7315646842237993430[138] = 0;
   out_7315646842237993430[139] = 0;
   out_7315646842237993430[140] = 0;
   out_7315646842237993430[141] = 0;
   out_7315646842237993430[142] = 0;
   out_7315646842237993430[143] = 0;
   out_7315646842237993430[144] = 0;
   out_7315646842237993430[145] = 0;
   out_7315646842237993430[146] = 0;
   out_7315646842237993430[147] = 0;
   out_7315646842237993430[148] = 0;
   out_7315646842237993430[149] = 0;
   out_7315646842237993430[150] = 0;
   out_7315646842237993430[151] = 0;
   out_7315646842237993430[152] = 1;
   out_7315646842237993430[153] = 0;
   out_7315646842237993430[154] = 0;
   out_7315646842237993430[155] = 0;
   out_7315646842237993430[156] = 0;
   out_7315646842237993430[157] = 0;
   out_7315646842237993430[158] = 0;
   out_7315646842237993430[159] = 0;
   out_7315646842237993430[160] = 0;
   out_7315646842237993430[161] = 0;
   out_7315646842237993430[162] = 0;
   out_7315646842237993430[163] = 0;
   out_7315646842237993430[164] = 0;
   out_7315646842237993430[165] = 0;
   out_7315646842237993430[166] = 0;
   out_7315646842237993430[167] = 0;
   out_7315646842237993430[168] = 0;
   out_7315646842237993430[169] = 0;
   out_7315646842237993430[170] = 0;
   out_7315646842237993430[171] = 1;
   out_7315646842237993430[172] = 0;
   out_7315646842237993430[173] = 0;
   out_7315646842237993430[174] = 0;
   out_7315646842237993430[175] = 0;
   out_7315646842237993430[176] = 0;
   out_7315646842237993430[177] = 0;
   out_7315646842237993430[178] = 0;
   out_7315646842237993430[179] = 0;
   out_7315646842237993430[180] = 0;
   out_7315646842237993430[181] = 0;
   out_7315646842237993430[182] = 0;
   out_7315646842237993430[183] = 0;
   out_7315646842237993430[184] = 0;
   out_7315646842237993430[185] = 0;
   out_7315646842237993430[186] = 0;
   out_7315646842237993430[187] = 0;
   out_7315646842237993430[188] = 0;
   out_7315646842237993430[189] = 0;
   out_7315646842237993430[190] = 1;
   out_7315646842237993430[191] = 0;
   out_7315646842237993430[192] = 0;
   out_7315646842237993430[193] = 0;
   out_7315646842237993430[194] = 0;
   out_7315646842237993430[195] = 0;
   out_7315646842237993430[196] = 0;
   out_7315646842237993430[197] = 0;
   out_7315646842237993430[198] = 0;
   out_7315646842237993430[199] = 0;
   out_7315646842237993430[200] = 0;
   out_7315646842237993430[201] = 0;
   out_7315646842237993430[202] = 0;
   out_7315646842237993430[203] = 0;
   out_7315646842237993430[204] = 0;
   out_7315646842237993430[205] = 0;
   out_7315646842237993430[206] = 0;
   out_7315646842237993430[207] = 0;
   out_7315646842237993430[208] = 0;
   out_7315646842237993430[209] = 1;
   out_7315646842237993430[210] = 0;
   out_7315646842237993430[211] = 0;
   out_7315646842237993430[212] = 0;
   out_7315646842237993430[213] = 0;
   out_7315646842237993430[214] = 0;
   out_7315646842237993430[215] = 0;
   out_7315646842237993430[216] = 0;
   out_7315646842237993430[217] = 0;
   out_7315646842237993430[218] = 0;
   out_7315646842237993430[219] = 0;
   out_7315646842237993430[220] = 0;
   out_7315646842237993430[221] = 0;
   out_7315646842237993430[222] = 0;
   out_7315646842237993430[223] = 0;
   out_7315646842237993430[224] = 0;
   out_7315646842237993430[225] = 0;
   out_7315646842237993430[226] = 0;
   out_7315646842237993430[227] = 0;
   out_7315646842237993430[228] = 1;
   out_7315646842237993430[229] = 0;
   out_7315646842237993430[230] = 0;
   out_7315646842237993430[231] = 0;
   out_7315646842237993430[232] = 0;
   out_7315646842237993430[233] = 0;
   out_7315646842237993430[234] = 0;
   out_7315646842237993430[235] = 0;
   out_7315646842237993430[236] = 0;
   out_7315646842237993430[237] = 0;
   out_7315646842237993430[238] = 0;
   out_7315646842237993430[239] = 0;
   out_7315646842237993430[240] = 0;
   out_7315646842237993430[241] = 0;
   out_7315646842237993430[242] = 0;
   out_7315646842237993430[243] = 0;
   out_7315646842237993430[244] = 0;
   out_7315646842237993430[245] = 0;
   out_7315646842237993430[246] = 0;
   out_7315646842237993430[247] = 1;
   out_7315646842237993430[248] = 0;
   out_7315646842237993430[249] = 0;
   out_7315646842237993430[250] = 0;
   out_7315646842237993430[251] = 0;
   out_7315646842237993430[252] = 0;
   out_7315646842237993430[253] = 0;
   out_7315646842237993430[254] = 0;
   out_7315646842237993430[255] = 0;
   out_7315646842237993430[256] = 0;
   out_7315646842237993430[257] = 0;
   out_7315646842237993430[258] = 0;
   out_7315646842237993430[259] = 0;
   out_7315646842237993430[260] = 0;
   out_7315646842237993430[261] = 0;
   out_7315646842237993430[262] = 0;
   out_7315646842237993430[263] = 0;
   out_7315646842237993430[264] = 0;
   out_7315646842237993430[265] = 0;
   out_7315646842237993430[266] = 1;
   out_7315646842237993430[267] = 0;
   out_7315646842237993430[268] = 0;
   out_7315646842237993430[269] = 0;
   out_7315646842237993430[270] = 0;
   out_7315646842237993430[271] = 0;
   out_7315646842237993430[272] = 0;
   out_7315646842237993430[273] = 0;
   out_7315646842237993430[274] = 0;
   out_7315646842237993430[275] = 0;
   out_7315646842237993430[276] = 0;
   out_7315646842237993430[277] = 0;
   out_7315646842237993430[278] = 0;
   out_7315646842237993430[279] = 0;
   out_7315646842237993430[280] = 0;
   out_7315646842237993430[281] = 0;
   out_7315646842237993430[282] = 0;
   out_7315646842237993430[283] = 0;
   out_7315646842237993430[284] = 0;
   out_7315646842237993430[285] = 1;
   out_7315646842237993430[286] = 0;
   out_7315646842237993430[287] = 0;
   out_7315646842237993430[288] = 0;
   out_7315646842237993430[289] = 0;
   out_7315646842237993430[290] = 0;
   out_7315646842237993430[291] = 0;
   out_7315646842237993430[292] = 0;
   out_7315646842237993430[293] = 0;
   out_7315646842237993430[294] = 0;
   out_7315646842237993430[295] = 0;
   out_7315646842237993430[296] = 0;
   out_7315646842237993430[297] = 0;
   out_7315646842237993430[298] = 0;
   out_7315646842237993430[299] = 0;
   out_7315646842237993430[300] = 0;
   out_7315646842237993430[301] = 0;
   out_7315646842237993430[302] = 0;
   out_7315646842237993430[303] = 0;
   out_7315646842237993430[304] = 1;
   out_7315646842237993430[305] = 0;
   out_7315646842237993430[306] = 0;
   out_7315646842237993430[307] = 0;
   out_7315646842237993430[308] = 0;
   out_7315646842237993430[309] = 0;
   out_7315646842237993430[310] = 0;
   out_7315646842237993430[311] = 0;
   out_7315646842237993430[312] = 0;
   out_7315646842237993430[313] = 0;
   out_7315646842237993430[314] = 0;
   out_7315646842237993430[315] = 0;
   out_7315646842237993430[316] = 0;
   out_7315646842237993430[317] = 0;
   out_7315646842237993430[318] = 0;
   out_7315646842237993430[319] = 0;
   out_7315646842237993430[320] = 0;
   out_7315646842237993430[321] = 0;
   out_7315646842237993430[322] = 0;
   out_7315646842237993430[323] = 1;
}
void h_4(double *state, double *unused, double *out_2905496685476842828) {
   out_2905496685476842828[0] = state[6] + state[9];
   out_2905496685476842828[1] = state[7] + state[10];
   out_2905496685476842828[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_5413221642194876961) {
   out_5413221642194876961[0] = 0;
   out_5413221642194876961[1] = 0;
   out_5413221642194876961[2] = 0;
   out_5413221642194876961[3] = 0;
   out_5413221642194876961[4] = 0;
   out_5413221642194876961[5] = 0;
   out_5413221642194876961[6] = 1;
   out_5413221642194876961[7] = 0;
   out_5413221642194876961[8] = 0;
   out_5413221642194876961[9] = 1;
   out_5413221642194876961[10] = 0;
   out_5413221642194876961[11] = 0;
   out_5413221642194876961[12] = 0;
   out_5413221642194876961[13] = 0;
   out_5413221642194876961[14] = 0;
   out_5413221642194876961[15] = 0;
   out_5413221642194876961[16] = 0;
   out_5413221642194876961[17] = 0;
   out_5413221642194876961[18] = 0;
   out_5413221642194876961[19] = 0;
   out_5413221642194876961[20] = 0;
   out_5413221642194876961[21] = 0;
   out_5413221642194876961[22] = 0;
   out_5413221642194876961[23] = 0;
   out_5413221642194876961[24] = 0;
   out_5413221642194876961[25] = 1;
   out_5413221642194876961[26] = 0;
   out_5413221642194876961[27] = 0;
   out_5413221642194876961[28] = 1;
   out_5413221642194876961[29] = 0;
   out_5413221642194876961[30] = 0;
   out_5413221642194876961[31] = 0;
   out_5413221642194876961[32] = 0;
   out_5413221642194876961[33] = 0;
   out_5413221642194876961[34] = 0;
   out_5413221642194876961[35] = 0;
   out_5413221642194876961[36] = 0;
   out_5413221642194876961[37] = 0;
   out_5413221642194876961[38] = 0;
   out_5413221642194876961[39] = 0;
   out_5413221642194876961[40] = 0;
   out_5413221642194876961[41] = 0;
   out_5413221642194876961[42] = 0;
   out_5413221642194876961[43] = 0;
   out_5413221642194876961[44] = 1;
   out_5413221642194876961[45] = 0;
   out_5413221642194876961[46] = 0;
   out_5413221642194876961[47] = 1;
   out_5413221642194876961[48] = 0;
   out_5413221642194876961[49] = 0;
   out_5413221642194876961[50] = 0;
   out_5413221642194876961[51] = 0;
   out_5413221642194876961[52] = 0;
   out_5413221642194876961[53] = 0;
}
void h_10(double *state, double *unused, double *out_1191620274986758585) {
   out_1191620274986758585[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_1191620274986758585[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_1191620274986758585[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_1027777997724036985) {
   out_1027777997724036985[0] = 0;
   out_1027777997724036985[1] = 9.8100000000000005*cos(state[1]);
   out_1027777997724036985[2] = 0;
   out_1027777997724036985[3] = 0;
   out_1027777997724036985[4] = -state[8];
   out_1027777997724036985[5] = state[7];
   out_1027777997724036985[6] = 0;
   out_1027777997724036985[7] = state[5];
   out_1027777997724036985[8] = -state[4];
   out_1027777997724036985[9] = 0;
   out_1027777997724036985[10] = 0;
   out_1027777997724036985[11] = 0;
   out_1027777997724036985[12] = 1;
   out_1027777997724036985[13] = 0;
   out_1027777997724036985[14] = 0;
   out_1027777997724036985[15] = 1;
   out_1027777997724036985[16] = 0;
   out_1027777997724036985[17] = 0;
   out_1027777997724036985[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_1027777997724036985[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_1027777997724036985[20] = 0;
   out_1027777997724036985[21] = state[8];
   out_1027777997724036985[22] = 0;
   out_1027777997724036985[23] = -state[6];
   out_1027777997724036985[24] = -state[5];
   out_1027777997724036985[25] = 0;
   out_1027777997724036985[26] = state[3];
   out_1027777997724036985[27] = 0;
   out_1027777997724036985[28] = 0;
   out_1027777997724036985[29] = 0;
   out_1027777997724036985[30] = 0;
   out_1027777997724036985[31] = 1;
   out_1027777997724036985[32] = 0;
   out_1027777997724036985[33] = 0;
   out_1027777997724036985[34] = 1;
   out_1027777997724036985[35] = 0;
   out_1027777997724036985[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_1027777997724036985[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_1027777997724036985[38] = 0;
   out_1027777997724036985[39] = -state[7];
   out_1027777997724036985[40] = state[6];
   out_1027777997724036985[41] = 0;
   out_1027777997724036985[42] = state[4];
   out_1027777997724036985[43] = -state[3];
   out_1027777997724036985[44] = 0;
   out_1027777997724036985[45] = 0;
   out_1027777997724036985[46] = 0;
   out_1027777997724036985[47] = 0;
   out_1027777997724036985[48] = 0;
   out_1027777997724036985[49] = 0;
   out_1027777997724036985[50] = 1;
   out_1027777997724036985[51] = 0;
   out_1027777997724036985[52] = 0;
   out_1027777997724036985[53] = 1;
}
void h_13(double *state, double *unused, double *out_7783237198684558375) {
   out_7783237198684558375[0] = state[3];
   out_7783237198684558375[1] = state[4];
   out_7783237198684558375[2] = state[5];
}
void H_13(double *state, double *unused, double *out_2200947816862544160) {
   out_2200947816862544160[0] = 0;
   out_2200947816862544160[1] = 0;
   out_2200947816862544160[2] = 0;
   out_2200947816862544160[3] = 1;
   out_2200947816862544160[4] = 0;
   out_2200947816862544160[5] = 0;
   out_2200947816862544160[6] = 0;
   out_2200947816862544160[7] = 0;
   out_2200947816862544160[8] = 0;
   out_2200947816862544160[9] = 0;
   out_2200947816862544160[10] = 0;
   out_2200947816862544160[11] = 0;
   out_2200947816862544160[12] = 0;
   out_2200947816862544160[13] = 0;
   out_2200947816862544160[14] = 0;
   out_2200947816862544160[15] = 0;
   out_2200947816862544160[16] = 0;
   out_2200947816862544160[17] = 0;
   out_2200947816862544160[18] = 0;
   out_2200947816862544160[19] = 0;
   out_2200947816862544160[20] = 0;
   out_2200947816862544160[21] = 0;
   out_2200947816862544160[22] = 1;
   out_2200947816862544160[23] = 0;
   out_2200947816862544160[24] = 0;
   out_2200947816862544160[25] = 0;
   out_2200947816862544160[26] = 0;
   out_2200947816862544160[27] = 0;
   out_2200947816862544160[28] = 0;
   out_2200947816862544160[29] = 0;
   out_2200947816862544160[30] = 0;
   out_2200947816862544160[31] = 0;
   out_2200947816862544160[32] = 0;
   out_2200947816862544160[33] = 0;
   out_2200947816862544160[34] = 0;
   out_2200947816862544160[35] = 0;
   out_2200947816862544160[36] = 0;
   out_2200947816862544160[37] = 0;
   out_2200947816862544160[38] = 0;
   out_2200947816862544160[39] = 0;
   out_2200947816862544160[40] = 0;
   out_2200947816862544160[41] = 1;
   out_2200947816862544160[42] = 0;
   out_2200947816862544160[43] = 0;
   out_2200947816862544160[44] = 0;
   out_2200947816862544160[45] = 0;
   out_2200947816862544160[46] = 0;
   out_2200947816862544160[47] = 0;
   out_2200947816862544160[48] = 0;
   out_2200947816862544160[49] = 0;
   out_2200947816862544160[50] = 0;
   out_2200947816862544160[51] = 0;
   out_2200947816862544160[52] = 0;
   out_2200947816862544160[53] = 0;
}
void h_14(double *state, double *unused, double *out_1027602586512413166) {
   out_1027602586512413166[0] = state[6];
   out_1027602586512413166[1] = state[7];
   out_1027602586512413166[2] = state[8];
}
void H_14(double *state, double *unused, double *out_1449980785855392432) {
   out_1449980785855392432[0] = 0;
   out_1449980785855392432[1] = 0;
   out_1449980785855392432[2] = 0;
   out_1449980785855392432[3] = 0;
   out_1449980785855392432[4] = 0;
   out_1449980785855392432[5] = 0;
   out_1449980785855392432[6] = 1;
   out_1449980785855392432[7] = 0;
   out_1449980785855392432[8] = 0;
   out_1449980785855392432[9] = 0;
   out_1449980785855392432[10] = 0;
   out_1449980785855392432[11] = 0;
   out_1449980785855392432[12] = 0;
   out_1449980785855392432[13] = 0;
   out_1449980785855392432[14] = 0;
   out_1449980785855392432[15] = 0;
   out_1449980785855392432[16] = 0;
   out_1449980785855392432[17] = 0;
   out_1449980785855392432[18] = 0;
   out_1449980785855392432[19] = 0;
   out_1449980785855392432[20] = 0;
   out_1449980785855392432[21] = 0;
   out_1449980785855392432[22] = 0;
   out_1449980785855392432[23] = 0;
   out_1449980785855392432[24] = 0;
   out_1449980785855392432[25] = 1;
   out_1449980785855392432[26] = 0;
   out_1449980785855392432[27] = 0;
   out_1449980785855392432[28] = 0;
   out_1449980785855392432[29] = 0;
   out_1449980785855392432[30] = 0;
   out_1449980785855392432[31] = 0;
   out_1449980785855392432[32] = 0;
   out_1449980785855392432[33] = 0;
   out_1449980785855392432[34] = 0;
   out_1449980785855392432[35] = 0;
   out_1449980785855392432[36] = 0;
   out_1449980785855392432[37] = 0;
   out_1449980785855392432[38] = 0;
   out_1449980785855392432[39] = 0;
   out_1449980785855392432[40] = 0;
   out_1449980785855392432[41] = 0;
   out_1449980785855392432[42] = 0;
   out_1449980785855392432[43] = 0;
   out_1449980785855392432[44] = 1;
   out_1449980785855392432[45] = 0;
   out_1449980785855392432[46] = 0;
   out_1449980785855392432[47] = 0;
   out_1449980785855392432[48] = 0;
   out_1449980785855392432[49] = 0;
   out_1449980785855392432[50] = 0;
   out_1449980785855392432[51] = 0;
   out_1449980785855392432[52] = 0;
   out_1449980785855392432[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_5912021845748559900) {
  err_fun(nom_x, delta_x, out_5912021845748559900);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_8843896357291937735) {
  inv_err_fun(nom_x, true_x, out_8843896357291937735);
}
void pose_H_mod_fun(double *state, double *out_2688320196777535589) {
  H_mod_fun(state, out_2688320196777535589);
}
void pose_f_fun(double *state, double dt, double *out_650263676124789422) {
  f_fun(state,  dt, out_650263676124789422);
}
void pose_F_fun(double *state, double dt, double *out_7315646842237993430) {
  F_fun(state,  dt, out_7315646842237993430);
}
void pose_h_4(double *state, double *unused, double *out_2905496685476842828) {
  h_4(state, unused, out_2905496685476842828);
}
void pose_H_4(double *state, double *unused, double *out_5413221642194876961) {
  H_4(state, unused, out_5413221642194876961);
}
void pose_h_10(double *state, double *unused, double *out_1191620274986758585) {
  h_10(state, unused, out_1191620274986758585);
}
void pose_H_10(double *state, double *unused, double *out_1027777997724036985) {
  H_10(state, unused, out_1027777997724036985);
}
void pose_h_13(double *state, double *unused, double *out_7783237198684558375) {
  h_13(state, unused, out_7783237198684558375);
}
void pose_H_13(double *state, double *unused, double *out_2200947816862544160) {
  H_13(state, unused, out_2200947816862544160);
}
void pose_h_14(double *state, double *unused, double *out_1027602586512413166) {
  h_14(state, unused, out_1027602586512413166);
}
void pose_H_14(double *state, double *unused, double *out_1449980785855392432) {
  H_14(state, unused, out_1449980785855392432);
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
