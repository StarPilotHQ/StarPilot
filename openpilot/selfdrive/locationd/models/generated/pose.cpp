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
void err_fun(double *nom_x, double *delta_x, double *out_4313072671461028668) {
   out_4313072671461028668[0] = delta_x[0] + nom_x[0];
   out_4313072671461028668[1] = delta_x[1] + nom_x[1];
   out_4313072671461028668[2] = delta_x[2] + nom_x[2];
   out_4313072671461028668[3] = delta_x[3] + nom_x[3];
   out_4313072671461028668[4] = delta_x[4] + nom_x[4];
   out_4313072671461028668[5] = delta_x[5] + nom_x[5];
   out_4313072671461028668[6] = delta_x[6] + nom_x[6];
   out_4313072671461028668[7] = delta_x[7] + nom_x[7];
   out_4313072671461028668[8] = delta_x[8] + nom_x[8];
   out_4313072671461028668[9] = delta_x[9] + nom_x[9];
   out_4313072671461028668[10] = delta_x[10] + nom_x[10];
   out_4313072671461028668[11] = delta_x[11] + nom_x[11];
   out_4313072671461028668[12] = delta_x[12] + nom_x[12];
   out_4313072671461028668[13] = delta_x[13] + nom_x[13];
   out_4313072671461028668[14] = delta_x[14] + nom_x[14];
   out_4313072671461028668[15] = delta_x[15] + nom_x[15];
   out_4313072671461028668[16] = delta_x[16] + nom_x[16];
   out_4313072671461028668[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5665898331795518232) {
   out_5665898331795518232[0] = -nom_x[0] + true_x[0];
   out_5665898331795518232[1] = -nom_x[1] + true_x[1];
   out_5665898331795518232[2] = -nom_x[2] + true_x[2];
   out_5665898331795518232[3] = -nom_x[3] + true_x[3];
   out_5665898331795518232[4] = -nom_x[4] + true_x[4];
   out_5665898331795518232[5] = -nom_x[5] + true_x[5];
   out_5665898331795518232[6] = -nom_x[6] + true_x[6];
   out_5665898331795518232[7] = -nom_x[7] + true_x[7];
   out_5665898331795518232[8] = -nom_x[8] + true_x[8];
   out_5665898331795518232[9] = -nom_x[9] + true_x[9];
   out_5665898331795518232[10] = -nom_x[10] + true_x[10];
   out_5665898331795518232[11] = -nom_x[11] + true_x[11];
   out_5665898331795518232[12] = -nom_x[12] + true_x[12];
   out_5665898331795518232[13] = -nom_x[13] + true_x[13];
   out_5665898331795518232[14] = -nom_x[14] + true_x[14];
   out_5665898331795518232[15] = -nom_x[15] + true_x[15];
   out_5665898331795518232[16] = -nom_x[16] + true_x[16];
   out_5665898331795518232[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_8115099160002711194) {
   out_8115099160002711194[0] = 1.0;
   out_8115099160002711194[1] = 0.0;
   out_8115099160002711194[2] = 0.0;
   out_8115099160002711194[3] = 0.0;
   out_8115099160002711194[4] = 0.0;
   out_8115099160002711194[5] = 0.0;
   out_8115099160002711194[6] = 0.0;
   out_8115099160002711194[7] = 0.0;
   out_8115099160002711194[8] = 0.0;
   out_8115099160002711194[9] = 0.0;
   out_8115099160002711194[10] = 0.0;
   out_8115099160002711194[11] = 0.0;
   out_8115099160002711194[12] = 0.0;
   out_8115099160002711194[13] = 0.0;
   out_8115099160002711194[14] = 0.0;
   out_8115099160002711194[15] = 0.0;
   out_8115099160002711194[16] = 0.0;
   out_8115099160002711194[17] = 0.0;
   out_8115099160002711194[18] = 0.0;
   out_8115099160002711194[19] = 1.0;
   out_8115099160002711194[20] = 0.0;
   out_8115099160002711194[21] = 0.0;
   out_8115099160002711194[22] = 0.0;
   out_8115099160002711194[23] = 0.0;
   out_8115099160002711194[24] = 0.0;
   out_8115099160002711194[25] = 0.0;
   out_8115099160002711194[26] = 0.0;
   out_8115099160002711194[27] = 0.0;
   out_8115099160002711194[28] = 0.0;
   out_8115099160002711194[29] = 0.0;
   out_8115099160002711194[30] = 0.0;
   out_8115099160002711194[31] = 0.0;
   out_8115099160002711194[32] = 0.0;
   out_8115099160002711194[33] = 0.0;
   out_8115099160002711194[34] = 0.0;
   out_8115099160002711194[35] = 0.0;
   out_8115099160002711194[36] = 0.0;
   out_8115099160002711194[37] = 0.0;
   out_8115099160002711194[38] = 1.0;
   out_8115099160002711194[39] = 0.0;
   out_8115099160002711194[40] = 0.0;
   out_8115099160002711194[41] = 0.0;
   out_8115099160002711194[42] = 0.0;
   out_8115099160002711194[43] = 0.0;
   out_8115099160002711194[44] = 0.0;
   out_8115099160002711194[45] = 0.0;
   out_8115099160002711194[46] = 0.0;
   out_8115099160002711194[47] = 0.0;
   out_8115099160002711194[48] = 0.0;
   out_8115099160002711194[49] = 0.0;
   out_8115099160002711194[50] = 0.0;
   out_8115099160002711194[51] = 0.0;
   out_8115099160002711194[52] = 0.0;
   out_8115099160002711194[53] = 0.0;
   out_8115099160002711194[54] = 0.0;
   out_8115099160002711194[55] = 0.0;
   out_8115099160002711194[56] = 0.0;
   out_8115099160002711194[57] = 1.0;
   out_8115099160002711194[58] = 0.0;
   out_8115099160002711194[59] = 0.0;
   out_8115099160002711194[60] = 0.0;
   out_8115099160002711194[61] = 0.0;
   out_8115099160002711194[62] = 0.0;
   out_8115099160002711194[63] = 0.0;
   out_8115099160002711194[64] = 0.0;
   out_8115099160002711194[65] = 0.0;
   out_8115099160002711194[66] = 0.0;
   out_8115099160002711194[67] = 0.0;
   out_8115099160002711194[68] = 0.0;
   out_8115099160002711194[69] = 0.0;
   out_8115099160002711194[70] = 0.0;
   out_8115099160002711194[71] = 0.0;
   out_8115099160002711194[72] = 0.0;
   out_8115099160002711194[73] = 0.0;
   out_8115099160002711194[74] = 0.0;
   out_8115099160002711194[75] = 0.0;
   out_8115099160002711194[76] = 1.0;
   out_8115099160002711194[77] = 0.0;
   out_8115099160002711194[78] = 0.0;
   out_8115099160002711194[79] = 0.0;
   out_8115099160002711194[80] = 0.0;
   out_8115099160002711194[81] = 0.0;
   out_8115099160002711194[82] = 0.0;
   out_8115099160002711194[83] = 0.0;
   out_8115099160002711194[84] = 0.0;
   out_8115099160002711194[85] = 0.0;
   out_8115099160002711194[86] = 0.0;
   out_8115099160002711194[87] = 0.0;
   out_8115099160002711194[88] = 0.0;
   out_8115099160002711194[89] = 0.0;
   out_8115099160002711194[90] = 0.0;
   out_8115099160002711194[91] = 0.0;
   out_8115099160002711194[92] = 0.0;
   out_8115099160002711194[93] = 0.0;
   out_8115099160002711194[94] = 0.0;
   out_8115099160002711194[95] = 1.0;
   out_8115099160002711194[96] = 0.0;
   out_8115099160002711194[97] = 0.0;
   out_8115099160002711194[98] = 0.0;
   out_8115099160002711194[99] = 0.0;
   out_8115099160002711194[100] = 0.0;
   out_8115099160002711194[101] = 0.0;
   out_8115099160002711194[102] = 0.0;
   out_8115099160002711194[103] = 0.0;
   out_8115099160002711194[104] = 0.0;
   out_8115099160002711194[105] = 0.0;
   out_8115099160002711194[106] = 0.0;
   out_8115099160002711194[107] = 0.0;
   out_8115099160002711194[108] = 0.0;
   out_8115099160002711194[109] = 0.0;
   out_8115099160002711194[110] = 0.0;
   out_8115099160002711194[111] = 0.0;
   out_8115099160002711194[112] = 0.0;
   out_8115099160002711194[113] = 0.0;
   out_8115099160002711194[114] = 1.0;
   out_8115099160002711194[115] = 0.0;
   out_8115099160002711194[116] = 0.0;
   out_8115099160002711194[117] = 0.0;
   out_8115099160002711194[118] = 0.0;
   out_8115099160002711194[119] = 0.0;
   out_8115099160002711194[120] = 0.0;
   out_8115099160002711194[121] = 0.0;
   out_8115099160002711194[122] = 0.0;
   out_8115099160002711194[123] = 0.0;
   out_8115099160002711194[124] = 0.0;
   out_8115099160002711194[125] = 0.0;
   out_8115099160002711194[126] = 0.0;
   out_8115099160002711194[127] = 0.0;
   out_8115099160002711194[128] = 0.0;
   out_8115099160002711194[129] = 0.0;
   out_8115099160002711194[130] = 0.0;
   out_8115099160002711194[131] = 0.0;
   out_8115099160002711194[132] = 0.0;
   out_8115099160002711194[133] = 1.0;
   out_8115099160002711194[134] = 0.0;
   out_8115099160002711194[135] = 0.0;
   out_8115099160002711194[136] = 0.0;
   out_8115099160002711194[137] = 0.0;
   out_8115099160002711194[138] = 0.0;
   out_8115099160002711194[139] = 0.0;
   out_8115099160002711194[140] = 0.0;
   out_8115099160002711194[141] = 0.0;
   out_8115099160002711194[142] = 0.0;
   out_8115099160002711194[143] = 0.0;
   out_8115099160002711194[144] = 0.0;
   out_8115099160002711194[145] = 0.0;
   out_8115099160002711194[146] = 0.0;
   out_8115099160002711194[147] = 0.0;
   out_8115099160002711194[148] = 0.0;
   out_8115099160002711194[149] = 0.0;
   out_8115099160002711194[150] = 0.0;
   out_8115099160002711194[151] = 0.0;
   out_8115099160002711194[152] = 1.0;
   out_8115099160002711194[153] = 0.0;
   out_8115099160002711194[154] = 0.0;
   out_8115099160002711194[155] = 0.0;
   out_8115099160002711194[156] = 0.0;
   out_8115099160002711194[157] = 0.0;
   out_8115099160002711194[158] = 0.0;
   out_8115099160002711194[159] = 0.0;
   out_8115099160002711194[160] = 0.0;
   out_8115099160002711194[161] = 0.0;
   out_8115099160002711194[162] = 0.0;
   out_8115099160002711194[163] = 0.0;
   out_8115099160002711194[164] = 0.0;
   out_8115099160002711194[165] = 0.0;
   out_8115099160002711194[166] = 0.0;
   out_8115099160002711194[167] = 0.0;
   out_8115099160002711194[168] = 0.0;
   out_8115099160002711194[169] = 0.0;
   out_8115099160002711194[170] = 0.0;
   out_8115099160002711194[171] = 1.0;
   out_8115099160002711194[172] = 0.0;
   out_8115099160002711194[173] = 0.0;
   out_8115099160002711194[174] = 0.0;
   out_8115099160002711194[175] = 0.0;
   out_8115099160002711194[176] = 0.0;
   out_8115099160002711194[177] = 0.0;
   out_8115099160002711194[178] = 0.0;
   out_8115099160002711194[179] = 0.0;
   out_8115099160002711194[180] = 0.0;
   out_8115099160002711194[181] = 0.0;
   out_8115099160002711194[182] = 0.0;
   out_8115099160002711194[183] = 0.0;
   out_8115099160002711194[184] = 0.0;
   out_8115099160002711194[185] = 0.0;
   out_8115099160002711194[186] = 0.0;
   out_8115099160002711194[187] = 0.0;
   out_8115099160002711194[188] = 0.0;
   out_8115099160002711194[189] = 0.0;
   out_8115099160002711194[190] = 1.0;
   out_8115099160002711194[191] = 0.0;
   out_8115099160002711194[192] = 0.0;
   out_8115099160002711194[193] = 0.0;
   out_8115099160002711194[194] = 0.0;
   out_8115099160002711194[195] = 0.0;
   out_8115099160002711194[196] = 0.0;
   out_8115099160002711194[197] = 0.0;
   out_8115099160002711194[198] = 0.0;
   out_8115099160002711194[199] = 0.0;
   out_8115099160002711194[200] = 0.0;
   out_8115099160002711194[201] = 0.0;
   out_8115099160002711194[202] = 0.0;
   out_8115099160002711194[203] = 0.0;
   out_8115099160002711194[204] = 0.0;
   out_8115099160002711194[205] = 0.0;
   out_8115099160002711194[206] = 0.0;
   out_8115099160002711194[207] = 0.0;
   out_8115099160002711194[208] = 0.0;
   out_8115099160002711194[209] = 1.0;
   out_8115099160002711194[210] = 0.0;
   out_8115099160002711194[211] = 0.0;
   out_8115099160002711194[212] = 0.0;
   out_8115099160002711194[213] = 0.0;
   out_8115099160002711194[214] = 0.0;
   out_8115099160002711194[215] = 0.0;
   out_8115099160002711194[216] = 0.0;
   out_8115099160002711194[217] = 0.0;
   out_8115099160002711194[218] = 0.0;
   out_8115099160002711194[219] = 0.0;
   out_8115099160002711194[220] = 0.0;
   out_8115099160002711194[221] = 0.0;
   out_8115099160002711194[222] = 0.0;
   out_8115099160002711194[223] = 0.0;
   out_8115099160002711194[224] = 0.0;
   out_8115099160002711194[225] = 0.0;
   out_8115099160002711194[226] = 0.0;
   out_8115099160002711194[227] = 0.0;
   out_8115099160002711194[228] = 1.0;
   out_8115099160002711194[229] = 0.0;
   out_8115099160002711194[230] = 0.0;
   out_8115099160002711194[231] = 0.0;
   out_8115099160002711194[232] = 0.0;
   out_8115099160002711194[233] = 0.0;
   out_8115099160002711194[234] = 0.0;
   out_8115099160002711194[235] = 0.0;
   out_8115099160002711194[236] = 0.0;
   out_8115099160002711194[237] = 0.0;
   out_8115099160002711194[238] = 0.0;
   out_8115099160002711194[239] = 0.0;
   out_8115099160002711194[240] = 0.0;
   out_8115099160002711194[241] = 0.0;
   out_8115099160002711194[242] = 0.0;
   out_8115099160002711194[243] = 0.0;
   out_8115099160002711194[244] = 0.0;
   out_8115099160002711194[245] = 0.0;
   out_8115099160002711194[246] = 0.0;
   out_8115099160002711194[247] = 1.0;
   out_8115099160002711194[248] = 0.0;
   out_8115099160002711194[249] = 0.0;
   out_8115099160002711194[250] = 0.0;
   out_8115099160002711194[251] = 0.0;
   out_8115099160002711194[252] = 0.0;
   out_8115099160002711194[253] = 0.0;
   out_8115099160002711194[254] = 0.0;
   out_8115099160002711194[255] = 0.0;
   out_8115099160002711194[256] = 0.0;
   out_8115099160002711194[257] = 0.0;
   out_8115099160002711194[258] = 0.0;
   out_8115099160002711194[259] = 0.0;
   out_8115099160002711194[260] = 0.0;
   out_8115099160002711194[261] = 0.0;
   out_8115099160002711194[262] = 0.0;
   out_8115099160002711194[263] = 0.0;
   out_8115099160002711194[264] = 0.0;
   out_8115099160002711194[265] = 0.0;
   out_8115099160002711194[266] = 1.0;
   out_8115099160002711194[267] = 0.0;
   out_8115099160002711194[268] = 0.0;
   out_8115099160002711194[269] = 0.0;
   out_8115099160002711194[270] = 0.0;
   out_8115099160002711194[271] = 0.0;
   out_8115099160002711194[272] = 0.0;
   out_8115099160002711194[273] = 0.0;
   out_8115099160002711194[274] = 0.0;
   out_8115099160002711194[275] = 0.0;
   out_8115099160002711194[276] = 0.0;
   out_8115099160002711194[277] = 0.0;
   out_8115099160002711194[278] = 0.0;
   out_8115099160002711194[279] = 0.0;
   out_8115099160002711194[280] = 0.0;
   out_8115099160002711194[281] = 0.0;
   out_8115099160002711194[282] = 0.0;
   out_8115099160002711194[283] = 0.0;
   out_8115099160002711194[284] = 0.0;
   out_8115099160002711194[285] = 1.0;
   out_8115099160002711194[286] = 0.0;
   out_8115099160002711194[287] = 0.0;
   out_8115099160002711194[288] = 0.0;
   out_8115099160002711194[289] = 0.0;
   out_8115099160002711194[290] = 0.0;
   out_8115099160002711194[291] = 0.0;
   out_8115099160002711194[292] = 0.0;
   out_8115099160002711194[293] = 0.0;
   out_8115099160002711194[294] = 0.0;
   out_8115099160002711194[295] = 0.0;
   out_8115099160002711194[296] = 0.0;
   out_8115099160002711194[297] = 0.0;
   out_8115099160002711194[298] = 0.0;
   out_8115099160002711194[299] = 0.0;
   out_8115099160002711194[300] = 0.0;
   out_8115099160002711194[301] = 0.0;
   out_8115099160002711194[302] = 0.0;
   out_8115099160002711194[303] = 0.0;
   out_8115099160002711194[304] = 1.0;
   out_8115099160002711194[305] = 0.0;
   out_8115099160002711194[306] = 0.0;
   out_8115099160002711194[307] = 0.0;
   out_8115099160002711194[308] = 0.0;
   out_8115099160002711194[309] = 0.0;
   out_8115099160002711194[310] = 0.0;
   out_8115099160002711194[311] = 0.0;
   out_8115099160002711194[312] = 0.0;
   out_8115099160002711194[313] = 0.0;
   out_8115099160002711194[314] = 0.0;
   out_8115099160002711194[315] = 0.0;
   out_8115099160002711194[316] = 0.0;
   out_8115099160002711194[317] = 0.0;
   out_8115099160002711194[318] = 0.0;
   out_8115099160002711194[319] = 0.0;
   out_8115099160002711194[320] = 0.0;
   out_8115099160002711194[321] = 0.0;
   out_8115099160002711194[322] = 0.0;
   out_8115099160002711194[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_161625255094512239) {
   out_161625255094512239[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_161625255094512239[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_161625255094512239[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_161625255094512239[3] = dt*state[12] + state[3];
   out_161625255094512239[4] = dt*state[13] + state[4];
   out_161625255094512239[5] = dt*state[14] + state[5];
   out_161625255094512239[6] = state[6];
   out_161625255094512239[7] = state[7];
   out_161625255094512239[8] = state[8];
   out_161625255094512239[9] = state[9];
   out_161625255094512239[10] = state[10];
   out_161625255094512239[11] = state[11];
   out_161625255094512239[12] = state[12];
   out_161625255094512239[13] = state[13];
   out_161625255094512239[14] = state[14];
   out_161625255094512239[15] = state[15];
   out_161625255094512239[16] = state[16];
   out_161625255094512239[17] = state[17];
}
void F_fun(double *state, double dt, double *out_5522126784489152373) {
   out_5522126784489152373[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5522126784489152373[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5522126784489152373[2] = 0;
   out_5522126784489152373[3] = 0;
   out_5522126784489152373[4] = 0;
   out_5522126784489152373[5] = 0;
   out_5522126784489152373[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5522126784489152373[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5522126784489152373[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5522126784489152373[9] = 0;
   out_5522126784489152373[10] = 0;
   out_5522126784489152373[11] = 0;
   out_5522126784489152373[12] = 0;
   out_5522126784489152373[13] = 0;
   out_5522126784489152373[14] = 0;
   out_5522126784489152373[15] = 0;
   out_5522126784489152373[16] = 0;
   out_5522126784489152373[17] = 0;
   out_5522126784489152373[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5522126784489152373[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5522126784489152373[20] = 0;
   out_5522126784489152373[21] = 0;
   out_5522126784489152373[22] = 0;
   out_5522126784489152373[23] = 0;
   out_5522126784489152373[24] = 0;
   out_5522126784489152373[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5522126784489152373[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5522126784489152373[27] = 0;
   out_5522126784489152373[28] = 0;
   out_5522126784489152373[29] = 0;
   out_5522126784489152373[30] = 0;
   out_5522126784489152373[31] = 0;
   out_5522126784489152373[32] = 0;
   out_5522126784489152373[33] = 0;
   out_5522126784489152373[34] = 0;
   out_5522126784489152373[35] = 0;
   out_5522126784489152373[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5522126784489152373[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5522126784489152373[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5522126784489152373[39] = 0;
   out_5522126784489152373[40] = 0;
   out_5522126784489152373[41] = 0;
   out_5522126784489152373[42] = 0;
   out_5522126784489152373[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5522126784489152373[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5522126784489152373[45] = 0;
   out_5522126784489152373[46] = 0;
   out_5522126784489152373[47] = 0;
   out_5522126784489152373[48] = 0;
   out_5522126784489152373[49] = 0;
   out_5522126784489152373[50] = 0;
   out_5522126784489152373[51] = 0;
   out_5522126784489152373[52] = 0;
   out_5522126784489152373[53] = 0;
   out_5522126784489152373[54] = 0;
   out_5522126784489152373[55] = 0;
   out_5522126784489152373[56] = 0;
   out_5522126784489152373[57] = 1;
   out_5522126784489152373[58] = 0;
   out_5522126784489152373[59] = 0;
   out_5522126784489152373[60] = 0;
   out_5522126784489152373[61] = 0;
   out_5522126784489152373[62] = 0;
   out_5522126784489152373[63] = 0;
   out_5522126784489152373[64] = 0;
   out_5522126784489152373[65] = 0;
   out_5522126784489152373[66] = dt;
   out_5522126784489152373[67] = 0;
   out_5522126784489152373[68] = 0;
   out_5522126784489152373[69] = 0;
   out_5522126784489152373[70] = 0;
   out_5522126784489152373[71] = 0;
   out_5522126784489152373[72] = 0;
   out_5522126784489152373[73] = 0;
   out_5522126784489152373[74] = 0;
   out_5522126784489152373[75] = 0;
   out_5522126784489152373[76] = 1;
   out_5522126784489152373[77] = 0;
   out_5522126784489152373[78] = 0;
   out_5522126784489152373[79] = 0;
   out_5522126784489152373[80] = 0;
   out_5522126784489152373[81] = 0;
   out_5522126784489152373[82] = 0;
   out_5522126784489152373[83] = 0;
   out_5522126784489152373[84] = 0;
   out_5522126784489152373[85] = dt;
   out_5522126784489152373[86] = 0;
   out_5522126784489152373[87] = 0;
   out_5522126784489152373[88] = 0;
   out_5522126784489152373[89] = 0;
   out_5522126784489152373[90] = 0;
   out_5522126784489152373[91] = 0;
   out_5522126784489152373[92] = 0;
   out_5522126784489152373[93] = 0;
   out_5522126784489152373[94] = 0;
   out_5522126784489152373[95] = 1;
   out_5522126784489152373[96] = 0;
   out_5522126784489152373[97] = 0;
   out_5522126784489152373[98] = 0;
   out_5522126784489152373[99] = 0;
   out_5522126784489152373[100] = 0;
   out_5522126784489152373[101] = 0;
   out_5522126784489152373[102] = 0;
   out_5522126784489152373[103] = 0;
   out_5522126784489152373[104] = dt;
   out_5522126784489152373[105] = 0;
   out_5522126784489152373[106] = 0;
   out_5522126784489152373[107] = 0;
   out_5522126784489152373[108] = 0;
   out_5522126784489152373[109] = 0;
   out_5522126784489152373[110] = 0;
   out_5522126784489152373[111] = 0;
   out_5522126784489152373[112] = 0;
   out_5522126784489152373[113] = 0;
   out_5522126784489152373[114] = 1;
   out_5522126784489152373[115] = 0;
   out_5522126784489152373[116] = 0;
   out_5522126784489152373[117] = 0;
   out_5522126784489152373[118] = 0;
   out_5522126784489152373[119] = 0;
   out_5522126784489152373[120] = 0;
   out_5522126784489152373[121] = 0;
   out_5522126784489152373[122] = 0;
   out_5522126784489152373[123] = 0;
   out_5522126784489152373[124] = 0;
   out_5522126784489152373[125] = 0;
   out_5522126784489152373[126] = 0;
   out_5522126784489152373[127] = 0;
   out_5522126784489152373[128] = 0;
   out_5522126784489152373[129] = 0;
   out_5522126784489152373[130] = 0;
   out_5522126784489152373[131] = 0;
   out_5522126784489152373[132] = 0;
   out_5522126784489152373[133] = 1;
   out_5522126784489152373[134] = 0;
   out_5522126784489152373[135] = 0;
   out_5522126784489152373[136] = 0;
   out_5522126784489152373[137] = 0;
   out_5522126784489152373[138] = 0;
   out_5522126784489152373[139] = 0;
   out_5522126784489152373[140] = 0;
   out_5522126784489152373[141] = 0;
   out_5522126784489152373[142] = 0;
   out_5522126784489152373[143] = 0;
   out_5522126784489152373[144] = 0;
   out_5522126784489152373[145] = 0;
   out_5522126784489152373[146] = 0;
   out_5522126784489152373[147] = 0;
   out_5522126784489152373[148] = 0;
   out_5522126784489152373[149] = 0;
   out_5522126784489152373[150] = 0;
   out_5522126784489152373[151] = 0;
   out_5522126784489152373[152] = 1;
   out_5522126784489152373[153] = 0;
   out_5522126784489152373[154] = 0;
   out_5522126784489152373[155] = 0;
   out_5522126784489152373[156] = 0;
   out_5522126784489152373[157] = 0;
   out_5522126784489152373[158] = 0;
   out_5522126784489152373[159] = 0;
   out_5522126784489152373[160] = 0;
   out_5522126784489152373[161] = 0;
   out_5522126784489152373[162] = 0;
   out_5522126784489152373[163] = 0;
   out_5522126784489152373[164] = 0;
   out_5522126784489152373[165] = 0;
   out_5522126784489152373[166] = 0;
   out_5522126784489152373[167] = 0;
   out_5522126784489152373[168] = 0;
   out_5522126784489152373[169] = 0;
   out_5522126784489152373[170] = 0;
   out_5522126784489152373[171] = 1;
   out_5522126784489152373[172] = 0;
   out_5522126784489152373[173] = 0;
   out_5522126784489152373[174] = 0;
   out_5522126784489152373[175] = 0;
   out_5522126784489152373[176] = 0;
   out_5522126784489152373[177] = 0;
   out_5522126784489152373[178] = 0;
   out_5522126784489152373[179] = 0;
   out_5522126784489152373[180] = 0;
   out_5522126784489152373[181] = 0;
   out_5522126784489152373[182] = 0;
   out_5522126784489152373[183] = 0;
   out_5522126784489152373[184] = 0;
   out_5522126784489152373[185] = 0;
   out_5522126784489152373[186] = 0;
   out_5522126784489152373[187] = 0;
   out_5522126784489152373[188] = 0;
   out_5522126784489152373[189] = 0;
   out_5522126784489152373[190] = 1;
   out_5522126784489152373[191] = 0;
   out_5522126784489152373[192] = 0;
   out_5522126784489152373[193] = 0;
   out_5522126784489152373[194] = 0;
   out_5522126784489152373[195] = 0;
   out_5522126784489152373[196] = 0;
   out_5522126784489152373[197] = 0;
   out_5522126784489152373[198] = 0;
   out_5522126784489152373[199] = 0;
   out_5522126784489152373[200] = 0;
   out_5522126784489152373[201] = 0;
   out_5522126784489152373[202] = 0;
   out_5522126784489152373[203] = 0;
   out_5522126784489152373[204] = 0;
   out_5522126784489152373[205] = 0;
   out_5522126784489152373[206] = 0;
   out_5522126784489152373[207] = 0;
   out_5522126784489152373[208] = 0;
   out_5522126784489152373[209] = 1;
   out_5522126784489152373[210] = 0;
   out_5522126784489152373[211] = 0;
   out_5522126784489152373[212] = 0;
   out_5522126784489152373[213] = 0;
   out_5522126784489152373[214] = 0;
   out_5522126784489152373[215] = 0;
   out_5522126784489152373[216] = 0;
   out_5522126784489152373[217] = 0;
   out_5522126784489152373[218] = 0;
   out_5522126784489152373[219] = 0;
   out_5522126784489152373[220] = 0;
   out_5522126784489152373[221] = 0;
   out_5522126784489152373[222] = 0;
   out_5522126784489152373[223] = 0;
   out_5522126784489152373[224] = 0;
   out_5522126784489152373[225] = 0;
   out_5522126784489152373[226] = 0;
   out_5522126784489152373[227] = 0;
   out_5522126784489152373[228] = 1;
   out_5522126784489152373[229] = 0;
   out_5522126784489152373[230] = 0;
   out_5522126784489152373[231] = 0;
   out_5522126784489152373[232] = 0;
   out_5522126784489152373[233] = 0;
   out_5522126784489152373[234] = 0;
   out_5522126784489152373[235] = 0;
   out_5522126784489152373[236] = 0;
   out_5522126784489152373[237] = 0;
   out_5522126784489152373[238] = 0;
   out_5522126784489152373[239] = 0;
   out_5522126784489152373[240] = 0;
   out_5522126784489152373[241] = 0;
   out_5522126784489152373[242] = 0;
   out_5522126784489152373[243] = 0;
   out_5522126784489152373[244] = 0;
   out_5522126784489152373[245] = 0;
   out_5522126784489152373[246] = 0;
   out_5522126784489152373[247] = 1;
   out_5522126784489152373[248] = 0;
   out_5522126784489152373[249] = 0;
   out_5522126784489152373[250] = 0;
   out_5522126784489152373[251] = 0;
   out_5522126784489152373[252] = 0;
   out_5522126784489152373[253] = 0;
   out_5522126784489152373[254] = 0;
   out_5522126784489152373[255] = 0;
   out_5522126784489152373[256] = 0;
   out_5522126784489152373[257] = 0;
   out_5522126784489152373[258] = 0;
   out_5522126784489152373[259] = 0;
   out_5522126784489152373[260] = 0;
   out_5522126784489152373[261] = 0;
   out_5522126784489152373[262] = 0;
   out_5522126784489152373[263] = 0;
   out_5522126784489152373[264] = 0;
   out_5522126784489152373[265] = 0;
   out_5522126784489152373[266] = 1;
   out_5522126784489152373[267] = 0;
   out_5522126784489152373[268] = 0;
   out_5522126784489152373[269] = 0;
   out_5522126784489152373[270] = 0;
   out_5522126784489152373[271] = 0;
   out_5522126784489152373[272] = 0;
   out_5522126784489152373[273] = 0;
   out_5522126784489152373[274] = 0;
   out_5522126784489152373[275] = 0;
   out_5522126784489152373[276] = 0;
   out_5522126784489152373[277] = 0;
   out_5522126784489152373[278] = 0;
   out_5522126784489152373[279] = 0;
   out_5522126784489152373[280] = 0;
   out_5522126784489152373[281] = 0;
   out_5522126784489152373[282] = 0;
   out_5522126784489152373[283] = 0;
   out_5522126784489152373[284] = 0;
   out_5522126784489152373[285] = 1;
   out_5522126784489152373[286] = 0;
   out_5522126784489152373[287] = 0;
   out_5522126784489152373[288] = 0;
   out_5522126784489152373[289] = 0;
   out_5522126784489152373[290] = 0;
   out_5522126784489152373[291] = 0;
   out_5522126784489152373[292] = 0;
   out_5522126784489152373[293] = 0;
   out_5522126784489152373[294] = 0;
   out_5522126784489152373[295] = 0;
   out_5522126784489152373[296] = 0;
   out_5522126784489152373[297] = 0;
   out_5522126784489152373[298] = 0;
   out_5522126784489152373[299] = 0;
   out_5522126784489152373[300] = 0;
   out_5522126784489152373[301] = 0;
   out_5522126784489152373[302] = 0;
   out_5522126784489152373[303] = 0;
   out_5522126784489152373[304] = 1;
   out_5522126784489152373[305] = 0;
   out_5522126784489152373[306] = 0;
   out_5522126784489152373[307] = 0;
   out_5522126784489152373[308] = 0;
   out_5522126784489152373[309] = 0;
   out_5522126784489152373[310] = 0;
   out_5522126784489152373[311] = 0;
   out_5522126784489152373[312] = 0;
   out_5522126784489152373[313] = 0;
   out_5522126784489152373[314] = 0;
   out_5522126784489152373[315] = 0;
   out_5522126784489152373[316] = 0;
   out_5522126784489152373[317] = 0;
   out_5522126784489152373[318] = 0;
   out_5522126784489152373[319] = 0;
   out_5522126784489152373[320] = 0;
   out_5522126784489152373[321] = 0;
   out_5522126784489152373[322] = 0;
   out_5522126784489152373[323] = 1;
}
void h_4(double *state, double *unused, double *out_8968127760178561142) {
   out_8968127760178561142[0] = state[6] + state[9];
   out_8968127760178561142[1] = state[7] + state[10];
   out_8968127760178561142[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_1329011411857332767) {
   out_1329011411857332767[0] = 0;
   out_1329011411857332767[1] = 0;
   out_1329011411857332767[2] = 0;
   out_1329011411857332767[3] = 0;
   out_1329011411857332767[4] = 0;
   out_1329011411857332767[5] = 0;
   out_1329011411857332767[6] = 1;
   out_1329011411857332767[7] = 0;
   out_1329011411857332767[8] = 0;
   out_1329011411857332767[9] = 1;
   out_1329011411857332767[10] = 0;
   out_1329011411857332767[11] = 0;
   out_1329011411857332767[12] = 0;
   out_1329011411857332767[13] = 0;
   out_1329011411857332767[14] = 0;
   out_1329011411857332767[15] = 0;
   out_1329011411857332767[16] = 0;
   out_1329011411857332767[17] = 0;
   out_1329011411857332767[18] = 0;
   out_1329011411857332767[19] = 0;
   out_1329011411857332767[20] = 0;
   out_1329011411857332767[21] = 0;
   out_1329011411857332767[22] = 0;
   out_1329011411857332767[23] = 0;
   out_1329011411857332767[24] = 0;
   out_1329011411857332767[25] = 1;
   out_1329011411857332767[26] = 0;
   out_1329011411857332767[27] = 0;
   out_1329011411857332767[28] = 1;
   out_1329011411857332767[29] = 0;
   out_1329011411857332767[30] = 0;
   out_1329011411857332767[31] = 0;
   out_1329011411857332767[32] = 0;
   out_1329011411857332767[33] = 0;
   out_1329011411857332767[34] = 0;
   out_1329011411857332767[35] = 0;
   out_1329011411857332767[36] = 0;
   out_1329011411857332767[37] = 0;
   out_1329011411857332767[38] = 0;
   out_1329011411857332767[39] = 0;
   out_1329011411857332767[40] = 0;
   out_1329011411857332767[41] = 0;
   out_1329011411857332767[42] = 0;
   out_1329011411857332767[43] = 0;
   out_1329011411857332767[44] = 1;
   out_1329011411857332767[45] = 0;
   out_1329011411857332767[46] = 0;
   out_1329011411857332767[47] = 1;
   out_1329011411857332767[48] = 0;
   out_1329011411857332767[49] = 0;
   out_1329011411857332767[50] = 0;
   out_1329011411857332767[51] = 0;
   out_1329011411857332767[52] = 0;
   out_1329011411857332767[53] = 0;
}
void h_10(double *state, double *unused, double *out_2424973759049590814) {
   out_2424973759049590814[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_2424973759049590814[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_2424973759049590814[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_3789013634481263333) {
   out_3789013634481263333[0] = 0;
   out_3789013634481263333[1] = 9.8100000000000005*cos(state[1]);
   out_3789013634481263333[2] = 0;
   out_3789013634481263333[3] = 0;
   out_3789013634481263333[4] = -state[8];
   out_3789013634481263333[5] = state[7];
   out_3789013634481263333[6] = 0;
   out_3789013634481263333[7] = state[5];
   out_3789013634481263333[8] = -state[4];
   out_3789013634481263333[9] = 0;
   out_3789013634481263333[10] = 0;
   out_3789013634481263333[11] = 0;
   out_3789013634481263333[12] = 1;
   out_3789013634481263333[13] = 0;
   out_3789013634481263333[14] = 0;
   out_3789013634481263333[15] = 1;
   out_3789013634481263333[16] = 0;
   out_3789013634481263333[17] = 0;
   out_3789013634481263333[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_3789013634481263333[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_3789013634481263333[20] = 0;
   out_3789013634481263333[21] = state[8];
   out_3789013634481263333[22] = 0;
   out_3789013634481263333[23] = -state[6];
   out_3789013634481263333[24] = -state[5];
   out_3789013634481263333[25] = 0;
   out_3789013634481263333[26] = state[3];
   out_3789013634481263333[27] = 0;
   out_3789013634481263333[28] = 0;
   out_3789013634481263333[29] = 0;
   out_3789013634481263333[30] = 0;
   out_3789013634481263333[31] = 1;
   out_3789013634481263333[32] = 0;
   out_3789013634481263333[33] = 0;
   out_3789013634481263333[34] = 1;
   out_3789013634481263333[35] = 0;
   out_3789013634481263333[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_3789013634481263333[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_3789013634481263333[38] = 0;
   out_3789013634481263333[39] = -state[7];
   out_3789013634481263333[40] = state[6];
   out_3789013634481263333[41] = 0;
   out_3789013634481263333[42] = state[4];
   out_3789013634481263333[43] = -state[3];
   out_3789013634481263333[44] = 0;
   out_3789013634481263333[45] = 0;
   out_3789013634481263333[46] = 0;
   out_3789013634481263333[47] = 0;
   out_3789013634481263333[48] = 0;
   out_3789013634481263333[49] = 0;
   out_3789013634481263333[50] = 1;
   out_3789013634481263333[51] = 0;
   out_3789013634481263333[52] = 0;
   out_3789013634481263333[53] = 1;
}
void h_13(double *state, double *unused, double *out_1545401052236172922) {
   out_1545401052236172922[0] = state[3];
   out_1545401052236172922[1] = state[4];
   out_1545401052236172922[2] = state[5];
}
void H_13(double *state, double *unused, double *out_4541285237189665568) {
   out_4541285237189665568[0] = 0;
   out_4541285237189665568[1] = 0;
   out_4541285237189665568[2] = 0;
   out_4541285237189665568[3] = 1;
   out_4541285237189665568[4] = 0;
   out_4541285237189665568[5] = 0;
   out_4541285237189665568[6] = 0;
   out_4541285237189665568[7] = 0;
   out_4541285237189665568[8] = 0;
   out_4541285237189665568[9] = 0;
   out_4541285237189665568[10] = 0;
   out_4541285237189665568[11] = 0;
   out_4541285237189665568[12] = 0;
   out_4541285237189665568[13] = 0;
   out_4541285237189665568[14] = 0;
   out_4541285237189665568[15] = 0;
   out_4541285237189665568[16] = 0;
   out_4541285237189665568[17] = 0;
   out_4541285237189665568[18] = 0;
   out_4541285237189665568[19] = 0;
   out_4541285237189665568[20] = 0;
   out_4541285237189665568[21] = 0;
   out_4541285237189665568[22] = 1;
   out_4541285237189665568[23] = 0;
   out_4541285237189665568[24] = 0;
   out_4541285237189665568[25] = 0;
   out_4541285237189665568[26] = 0;
   out_4541285237189665568[27] = 0;
   out_4541285237189665568[28] = 0;
   out_4541285237189665568[29] = 0;
   out_4541285237189665568[30] = 0;
   out_4541285237189665568[31] = 0;
   out_4541285237189665568[32] = 0;
   out_4541285237189665568[33] = 0;
   out_4541285237189665568[34] = 0;
   out_4541285237189665568[35] = 0;
   out_4541285237189665568[36] = 0;
   out_4541285237189665568[37] = 0;
   out_4541285237189665568[38] = 0;
   out_4541285237189665568[39] = 0;
   out_4541285237189665568[40] = 0;
   out_4541285237189665568[41] = 1;
   out_4541285237189665568[42] = 0;
   out_4541285237189665568[43] = 0;
   out_4541285237189665568[44] = 0;
   out_4541285237189665568[45] = 0;
   out_4541285237189665568[46] = 0;
   out_4541285237189665568[47] = 0;
   out_4541285237189665568[48] = 0;
   out_4541285237189665568[49] = 0;
   out_4541285237189665568[50] = 0;
   out_4541285237189665568[51] = 0;
   out_4541285237189665568[52] = 0;
   out_4541285237189665568[53] = 0;
}
void h_14(double *state, double *unused, double *out_3039948735172272551) {
   out_3039948735172272551[0] = state[6];
   out_3039948735172272551[1] = state[7];
   out_3039948735172272551[2] = state[8];
}
void H_14(double *state, double *unused, double *out_1753777020438039529) {
   out_1753777020438039529[0] = 0;
   out_1753777020438039529[1] = 0;
   out_1753777020438039529[2] = 0;
   out_1753777020438039529[3] = 0;
   out_1753777020438039529[4] = 0;
   out_1753777020438039529[5] = 0;
   out_1753777020438039529[6] = 1;
   out_1753777020438039529[7] = 0;
   out_1753777020438039529[8] = 0;
   out_1753777020438039529[9] = 0;
   out_1753777020438039529[10] = 0;
   out_1753777020438039529[11] = 0;
   out_1753777020438039529[12] = 0;
   out_1753777020438039529[13] = 0;
   out_1753777020438039529[14] = 0;
   out_1753777020438039529[15] = 0;
   out_1753777020438039529[16] = 0;
   out_1753777020438039529[17] = 0;
   out_1753777020438039529[18] = 0;
   out_1753777020438039529[19] = 0;
   out_1753777020438039529[20] = 0;
   out_1753777020438039529[21] = 0;
   out_1753777020438039529[22] = 0;
   out_1753777020438039529[23] = 0;
   out_1753777020438039529[24] = 0;
   out_1753777020438039529[25] = 1;
   out_1753777020438039529[26] = 0;
   out_1753777020438039529[27] = 0;
   out_1753777020438039529[28] = 0;
   out_1753777020438039529[29] = 0;
   out_1753777020438039529[30] = 0;
   out_1753777020438039529[31] = 0;
   out_1753777020438039529[32] = 0;
   out_1753777020438039529[33] = 0;
   out_1753777020438039529[34] = 0;
   out_1753777020438039529[35] = 0;
   out_1753777020438039529[36] = 0;
   out_1753777020438039529[37] = 0;
   out_1753777020438039529[38] = 0;
   out_1753777020438039529[39] = 0;
   out_1753777020438039529[40] = 0;
   out_1753777020438039529[41] = 0;
   out_1753777020438039529[42] = 0;
   out_1753777020438039529[43] = 0;
   out_1753777020438039529[44] = 1;
   out_1753777020438039529[45] = 0;
   out_1753777020438039529[46] = 0;
   out_1753777020438039529[47] = 0;
   out_1753777020438039529[48] = 0;
   out_1753777020438039529[49] = 0;
   out_1753777020438039529[50] = 0;
   out_1753777020438039529[51] = 0;
   out_1753777020438039529[52] = 0;
   out_1753777020438039529[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_4313072671461028668) {
  err_fun(nom_x, delta_x, out_4313072671461028668);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5665898331795518232) {
  inv_err_fun(nom_x, true_x, out_5665898331795518232);
}
void pose_H_mod_fun(double *state, double *out_8115099160002711194) {
  H_mod_fun(state, out_8115099160002711194);
}
void pose_f_fun(double *state, double dt, double *out_161625255094512239) {
  f_fun(state,  dt, out_161625255094512239);
}
void pose_F_fun(double *state, double dt, double *out_5522126784489152373) {
  F_fun(state,  dt, out_5522126784489152373);
}
void pose_h_4(double *state, double *unused, double *out_8968127760178561142) {
  h_4(state, unused, out_8968127760178561142);
}
void pose_H_4(double *state, double *unused, double *out_1329011411857332767) {
  H_4(state, unused, out_1329011411857332767);
}
void pose_h_10(double *state, double *unused, double *out_2424973759049590814) {
  h_10(state, unused, out_2424973759049590814);
}
void pose_H_10(double *state, double *unused, double *out_3789013634481263333) {
  H_10(state, unused, out_3789013634481263333);
}
void pose_h_13(double *state, double *unused, double *out_1545401052236172922) {
  h_13(state, unused, out_1545401052236172922);
}
void pose_H_13(double *state, double *unused, double *out_4541285237189665568) {
  H_13(state, unused, out_4541285237189665568);
}
void pose_h_14(double *state, double *unused, double *out_3039948735172272551) {
  h_14(state, unused, out_3039948735172272551);
}
void pose_H_14(double *state, double *unused, double *out_1753777020438039529) {
  H_14(state, unused, out_1753777020438039529);
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
