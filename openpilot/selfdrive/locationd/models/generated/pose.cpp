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
void err_fun(double *nom_x, double *delta_x, double *out_8879109673270157512) {
   out_8879109673270157512[0] = delta_x[0] + nom_x[0];
   out_8879109673270157512[1] = delta_x[1] + nom_x[1];
   out_8879109673270157512[2] = delta_x[2] + nom_x[2];
   out_8879109673270157512[3] = delta_x[3] + nom_x[3];
   out_8879109673270157512[4] = delta_x[4] + nom_x[4];
   out_8879109673270157512[5] = delta_x[5] + nom_x[5];
   out_8879109673270157512[6] = delta_x[6] + nom_x[6];
   out_8879109673270157512[7] = delta_x[7] + nom_x[7];
   out_8879109673270157512[8] = delta_x[8] + nom_x[8];
   out_8879109673270157512[9] = delta_x[9] + nom_x[9];
   out_8879109673270157512[10] = delta_x[10] + nom_x[10];
   out_8879109673270157512[11] = delta_x[11] + nom_x[11];
   out_8879109673270157512[12] = delta_x[12] + nom_x[12];
   out_8879109673270157512[13] = delta_x[13] + nom_x[13];
   out_8879109673270157512[14] = delta_x[14] + nom_x[14];
   out_8879109673270157512[15] = delta_x[15] + nom_x[15];
   out_8879109673270157512[16] = delta_x[16] + nom_x[16];
   out_8879109673270157512[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_341167623382481477) {
   out_341167623382481477[0] = -nom_x[0] + true_x[0];
   out_341167623382481477[1] = -nom_x[1] + true_x[1];
   out_341167623382481477[2] = -nom_x[2] + true_x[2];
   out_341167623382481477[3] = -nom_x[3] + true_x[3];
   out_341167623382481477[4] = -nom_x[4] + true_x[4];
   out_341167623382481477[5] = -nom_x[5] + true_x[5];
   out_341167623382481477[6] = -nom_x[6] + true_x[6];
   out_341167623382481477[7] = -nom_x[7] + true_x[7];
   out_341167623382481477[8] = -nom_x[8] + true_x[8];
   out_341167623382481477[9] = -nom_x[9] + true_x[9];
   out_341167623382481477[10] = -nom_x[10] + true_x[10];
   out_341167623382481477[11] = -nom_x[11] + true_x[11];
   out_341167623382481477[12] = -nom_x[12] + true_x[12];
   out_341167623382481477[13] = -nom_x[13] + true_x[13];
   out_341167623382481477[14] = -nom_x[14] + true_x[14];
   out_341167623382481477[15] = -nom_x[15] + true_x[15];
   out_341167623382481477[16] = -nom_x[16] + true_x[16];
   out_341167623382481477[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_3336617953655085976) {
   out_3336617953655085976[0] = 1.0;
   out_3336617953655085976[1] = 0.0;
   out_3336617953655085976[2] = 0.0;
   out_3336617953655085976[3] = 0.0;
   out_3336617953655085976[4] = 0.0;
   out_3336617953655085976[5] = 0.0;
   out_3336617953655085976[6] = 0.0;
   out_3336617953655085976[7] = 0.0;
   out_3336617953655085976[8] = 0.0;
   out_3336617953655085976[9] = 0.0;
   out_3336617953655085976[10] = 0.0;
   out_3336617953655085976[11] = 0.0;
   out_3336617953655085976[12] = 0.0;
   out_3336617953655085976[13] = 0.0;
   out_3336617953655085976[14] = 0.0;
   out_3336617953655085976[15] = 0.0;
   out_3336617953655085976[16] = 0.0;
   out_3336617953655085976[17] = 0.0;
   out_3336617953655085976[18] = 0.0;
   out_3336617953655085976[19] = 1.0;
   out_3336617953655085976[20] = 0.0;
   out_3336617953655085976[21] = 0.0;
   out_3336617953655085976[22] = 0.0;
   out_3336617953655085976[23] = 0.0;
   out_3336617953655085976[24] = 0.0;
   out_3336617953655085976[25] = 0.0;
   out_3336617953655085976[26] = 0.0;
   out_3336617953655085976[27] = 0.0;
   out_3336617953655085976[28] = 0.0;
   out_3336617953655085976[29] = 0.0;
   out_3336617953655085976[30] = 0.0;
   out_3336617953655085976[31] = 0.0;
   out_3336617953655085976[32] = 0.0;
   out_3336617953655085976[33] = 0.0;
   out_3336617953655085976[34] = 0.0;
   out_3336617953655085976[35] = 0.0;
   out_3336617953655085976[36] = 0.0;
   out_3336617953655085976[37] = 0.0;
   out_3336617953655085976[38] = 1.0;
   out_3336617953655085976[39] = 0.0;
   out_3336617953655085976[40] = 0.0;
   out_3336617953655085976[41] = 0.0;
   out_3336617953655085976[42] = 0.0;
   out_3336617953655085976[43] = 0.0;
   out_3336617953655085976[44] = 0.0;
   out_3336617953655085976[45] = 0.0;
   out_3336617953655085976[46] = 0.0;
   out_3336617953655085976[47] = 0.0;
   out_3336617953655085976[48] = 0.0;
   out_3336617953655085976[49] = 0.0;
   out_3336617953655085976[50] = 0.0;
   out_3336617953655085976[51] = 0.0;
   out_3336617953655085976[52] = 0.0;
   out_3336617953655085976[53] = 0.0;
   out_3336617953655085976[54] = 0.0;
   out_3336617953655085976[55] = 0.0;
   out_3336617953655085976[56] = 0.0;
   out_3336617953655085976[57] = 1.0;
   out_3336617953655085976[58] = 0.0;
   out_3336617953655085976[59] = 0.0;
   out_3336617953655085976[60] = 0.0;
   out_3336617953655085976[61] = 0.0;
   out_3336617953655085976[62] = 0.0;
   out_3336617953655085976[63] = 0.0;
   out_3336617953655085976[64] = 0.0;
   out_3336617953655085976[65] = 0.0;
   out_3336617953655085976[66] = 0.0;
   out_3336617953655085976[67] = 0.0;
   out_3336617953655085976[68] = 0.0;
   out_3336617953655085976[69] = 0.0;
   out_3336617953655085976[70] = 0.0;
   out_3336617953655085976[71] = 0.0;
   out_3336617953655085976[72] = 0.0;
   out_3336617953655085976[73] = 0.0;
   out_3336617953655085976[74] = 0.0;
   out_3336617953655085976[75] = 0.0;
   out_3336617953655085976[76] = 1.0;
   out_3336617953655085976[77] = 0.0;
   out_3336617953655085976[78] = 0.0;
   out_3336617953655085976[79] = 0.0;
   out_3336617953655085976[80] = 0.0;
   out_3336617953655085976[81] = 0.0;
   out_3336617953655085976[82] = 0.0;
   out_3336617953655085976[83] = 0.0;
   out_3336617953655085976[84] = 0.0;
   out_3336617953655085976[85] = 0.0;
   out_3336617953655085976[86] = 0.0;
   out_3336617953655085976[87] = 0.0;
   out_3336617953655085976[88] = 0.0;
   out_3336617953655085976[89] = 0.0;
   out_3336617953655085976[90] = 0.0;
   out_3336617953655085976[91] = 0.0;
   out_3336617953655085976[92] = 0.0;
   out_3336617953655085976[93] = 0.0;
   out_3336617953655085976[94] = 0.0;
   out_3336617953655085976[95] = 1.0;
   out_3336617953655085976[96] = 0.0;
   out_3336617953655085976[97] = 0.0;
   out_3336617953655085976[98] = 0.0;
   out_3336617953655085976[99] = 0.0;
   out_3336617953655085976[100] = 0.0;
   out_3336617953655085976[101] = 0.0;
   out_3336617953655085976[102] = 0.0;
   out_3336617953655085976[103] = 0.0;
   out_3336617953655085976[104] = 0.0;
   out_3336617953655085976[105] = 0.0;
   out_3336617953655085976[106] = 0.0;
   out_3336617953655085976[107] = 0.0;
   out_3336617953655085976[108] = 0.0;
   out_3336617953655085976[109] = 0.0;
   out_3336617953655085976[110] = 0.0;
   out_3336617953655085976[111] = 0.0;
   out_3336617953655085976[112] = 0.0;
   out_3336617953655085976[113] = 0.0;
   out_3336617953655085976[114] = 1.0;
   out_3336617953655085976[115] = 0.0;
   out_3336617953655085976[116] = 0.0;
   out_3336617953655085976[117] = 0.0;
   out_3336617953655085976[118] = 0.0;
   out_3336617953655085976[119] = 0.0;
   out_3336617953655085976[120] = 0.0;
   out_3336617953655085976[121] = 0.0;
   out_3336617953655085976[122] = 0.0;
   out_3336617953655085976[123] = 0.0;
   out_3336617953655085976[124] = 0.0;
   out_3336617953655085976[125] = 0.0;
   out_3336617953655085976[126] = 0.0;
   out_3336617953655085976[127] = 0.0;
   out_3336617953655085976[128] = 0.0;
   out_3336617953655085976[129] = 0.0;
   out_3336617953655085976[130] = 0.0;
   out_3336617953655085976[131] = 0.0;
   out_3336617953655085976[132] = 0.0;
   out_3336617953655085976[133] = 1.0;
   out_3336617953655085976[134] = 0.0;
   out_3336617953655085976[135] = 0.0;
   out_3336617953655085976[136] = 0.0;
   out_3336617953655085976[137] = 0.0;
   out_3336617953655085976[138] = 0.0;
   out_3336617953655085976[139] = 0.0;
   out_3336617953655085976[140] = 0.0;
   out_3336617953655085976[141] = 0.0;
   out_3336617953655085976[142] = 0.0;
   out_3336617953655085976[143] = 0.0;
   out_3336617953655085976[144] = 0.0;
   out_3336617953655085976[145] = 0.0;
   out_3336617953655085976[146] = 0.0;
   out_3336617953655085976[147] = 0.0;
   out_3336617953655085976[148] = 0.0;
   out_3336617953655085976[149] = 0.0;
   out_3336617953655085976[150] = 0.0;
   out_3336617953655085976[151] = 0.0;
   out_3336617953655085976[152] = 1.0;
   out_3336617953655085976[153] = 0.0;
   out_3336617953655085976[154] = 0.0;
   out_3336617953655085976[155] = 0.0;
   out_3336617953655085976[156] = 0.0;
   out_3336617953655085976[157] = 0.0;
   out_3336617953655085976[158] = 0.0;
   out_3336617953655085976[159] = 0.0;
   out_3336617953655085976[160] = 0.0;
   out_3336617953655085976[161] = 0.0;
   out_3336617953655085976[162] = 0.0;
   out_3336617953655085976[163] = 0.0;
   out_3336617953655085976[164] = 0.0;
   out_3336617953655085976[165] = 0.0;
   out_3336617953655085976[166] = 0.0;
   out_3336617953655085976[167] = 0.0;
   out_3336617953655085976[168] = 0.0;
   out_3336617953655085976[169] = 0.0;
   out_3336617953655085976[170] = 0.0;
   out_3336617953655085976[171] = 1.0;
   out_3336617953655085976[172] = 0.0;
   out_3336617953655085976[173] = 0.0;
   out_3336617953655085976[174] = 0.0;
   out_3336617953655085976[175] = 0.0;
   out_3336617953655085976[176] = 0.0;
   out_3336617953655085976[177] = 0.0;
   out_3336617953655085976[178] = 0.0;
   out_3336617953655085976[179] = 0.0;
   out_3336617953655085976[180] = 0.0;
   out_3336617953655085976[181] = 0.0;
   out_3336617953655085976[182] = 0.0;
   out_3336617953655085976[183] = 0.0;
   out_3336617953655085976[184] = 0.0;
   out_3336617953655085976[185] = 0.0;
   out_3336617953655085976[186] = 0.0;
   out_3336617953655085976[187] = 0.0;
   out_3336617953655085976[188] = 0.0;
   out_3336617953655085976[189] = 0.0;
   out_3336617953655085976[190] = 1.0;
   out_3336617953655085976[191] = 0.0;
   out_3336617953655085976[192] = 0.0;
   out_3336617953655085976[193] = 0.0;
   out_3336617953655085976[194] = 0.0;
   out_3336617953655085976[195] = 0.0;
   out_3336617953655085976[196] = 0.0;
   out_3336617953655085976[197] = 0.0;
   out_3336617953655085976[198] = 0.0;
   out_3336617953655085976[199] = 0.0;
   out_3336617953655085976[200] = 0.0;
   out_3336617953655085976[201] = 0.0;
   out_3336617953655085976[202] = 0.0;
   out_3336617953655085976[203] = 0.0;
   out_3336617953655085976[204] = 0.0;
   out_3336617953655085976[205] = 0.0;
   out_3336617953655085976[206] = 0.0;
   out_3336617953655085976[207] = 0.0;
   out_3336617953655085976[208] = 0.0;
   out_3336617953655085976[209] = 1.0;
   out_3336617953655085976[210] = 0.0;
   out_3336617953655085976[211] = 0.0;
   out_3336617953655085976[212] = 0.0;
   out_3336617953655085976[213] = 0.0;
   out_3336617953655085976[214] = 0.0;
   out_3336617953655085976[215] = 0.0;
   out_3336617953655085976[216] = 0.0;
   out_3336617953655085976[217] = 0.0;
   out_3336617953655085976[218] = 0.0;
   out_3336617953655085976[219] = 0.0;
   out_3336617953655085976[220] = 0.0;
   out_3336617953655085976[221] = 0.0;
   out_3336617953655085976[222] = 0.0;
   out_3336617953655085976[223] = 0.0;
   out_3336617953655085976[224] = 0.0;
   out_3336617953655085976[225] = 0.0;
   out_3336617953655085976[226] = 0.0;
   out_3336617953655085976[227] = 0.0;
   out_3336617953655085976[228] = 1.0;
   out_3336617953655085976[229] = 0.0;
   out_3336617953655085976[230] = 0.0;
   out_3336617953655085976[231] = 0.0;
   out_3336617953655085976[232] = 0.0;
   out_3336617953655085976[233] = 0.0;
   out_3336617953655085976[234] = 0.0;
   out_3336617953655085976[235] = 0.0;
   out_3336617953655085976[236] = 0.0;
   out_3336617953655085976[237] = 0.0;
   out_3336617953655085976[238] = 0.0;
   out_3336617953655085976[239] = 0.0;
   out_3336617953655085976[240] = 0.0;
   out_3336617953655085976[241] = 0.0;
   out_3336617953655085976[242] = 0.0;
   out_3336617953655085976[243] = 0.0;
   out_3336617953655085976[244] = 0.0;
   out_3336617953655085976[245] = 0.0;
   out_3336617953655085976[246] = 0.0;
   out_3336617953655085976[247] = 1.0;
   out_3336617953655085976[248] = 0.0;
   out_3336617953655085976[249] = 0.0;
   out_3336617953655085976[250] = 0.0;
   out_3336617953655085976[251] = 0.0;
   out_3336617953655085976[252] = 0.0;
   out_3336617953655085976[253] = 0.0;
   out_3336617953655085976[254] = 0.0;
   out_3336617953655085976[255] = 0.0;
   out_3336617953655085976[256] = 0.0;
   out_3336617953655085976[257] = 0.0;
   out_3336617953655085976[258] = 0.0;
   out_3336617953655085976[259] = 0.0;
   out_3336617953655085976[260] = 0.0;
   out_3336617953655085976[261] = 0.0;
   out_3336617953655085976[262] = 0.0;
   out_3336617953655085976[263] = 0.0;
   out_3336617953655085976[264] = 0.0;
   out_3336617953655085976[265] = 0.0;
   out_3336617953655085976[266] = 1.0;
   out_3336617953655085976[267] = 0.0;
   out_3336617953655085976[268] = 0.0;
   out_3336617953655085976[269] = 0.0;
   out_3336617953655085976[270] = 0.0;
   out_3336617953655085976[271] = 0.0;
   out_3336617953655085976[272] = 0.0;
   out_3336617953655085976[273] = 0.0;
   out_3336617953655085976[274] = 0.0;
   out_3336617953655085976[275] = 0.0;
   out_3336617953655085976[276] = 0.0;
   out_3336617953655085976[277] = 0.0;
   out_3336617953655085976[278] = 0.0;
   out_3336617953655085976[279] = 0.0;
   out_3336617953655085976[280] = 0.0;
   out_3336617953655085976[281] = 0.0;
   out_3336617953655085976[282] = 0.0;
   out_3336617953655085976[283] = 0.0;
   out_3336617953655085976[284] = 0.0;
   out_3336617953655085976[285] = 1.0;
   out_3336617953655085976[286] = 0.0;
   out_3336617953655085976[287] = 0.0;
   out_3336617953655085976[288] = 0.0;
   out_3336617953655085976[289] = 0.0;
   out_3336617953655085976[290] = 0.0;
   out_3336617953655085976[291] = 0.0;
   out_3336617953655085976[292] = 0.0;
   out_3336617953655085976[293] = 0.0;
   out_3336617953655085976[294] = 0.0;
   out_3336617953655085976[295] = 0.0;
   out_3336617953655085976[296] = 0.0;
   out_3336617953655085976[297] = 0.0;
   out_3336617953655085976[298] = 0.0;
   out_3336617953655085976[299] = 0.0;
   out_3336617953655085976[300] = 0.0;
   out_3336617953655085976[301] = 0.0;
   out_3336617953655085976[302] = 0.0;
   out_3336617953655085976[303] = 0.0;
   out_3336617953655085976[304] = 1.0;
   out_3336617953655085976[305] = 0.0;
   out_3336617953655085976[306] = 0.0;
   out_3336617953655085976[307] = 0.0;
   out_3336617953655085976[308] = 0.0;
   out_3336617953655085976[309] = 0.0;
   out_3336617953655085976[310] = 0.0;
   out_3336617953655085976[311] = 0.0;
   out_3336617953655085976[312] = 0.0;
   out_3336617953655085976[313] = 0.0;
   out_3336617953655085976[314] = 0.0;
   out_3336617953655085976[315] = 0.0;
   out_3336617953655085976[316] = 0.0;
   out_3336617953655085976[317] = 0.0;
   out_3336617953655085976[318] = 0.0;
   out_3336617953655085976[319] = 0.0;
   out_3336617953655085976[320] = 0.0;
   out_3336617953655085976[321] = 0.0;
   out_3336617953655085976[322] = 0.0;
   out_3336617953655085976[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_8832833102796243366) {
   out_8832833102796243366[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_8832833102796243366[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_8832833102796243366[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_8832833102796243366[3] = dt*state[12] + state[3];
   out_8832833102796243366[4] = dt*state[13] + state[4];
   out_8832833102796243366[5] = dt*state[14] + state[5];
   out_8832833102796243366[6] = state[6];
   out_8832833102796243366[7] = state[7];
   out_8832833102796243366[8] = state[8];
   out_8832833102796243366[9] = state[9];
   out_8832833102796243366[10] = state[10];
   out_8832833102796243366[11] = state[11];
   out_8832833102796243366[12] = state[12];
   out_8832833102796243366[13] = state[13];
   out_8832833102796243366[14] = state[14];
   out_8832833102796243366[15] = state[15];
   out_8832833102796243366[16] = state[16];
   out_8832833102796243366[17] = state[17];
}
void F_fun(double *state, double dt, double *out_3295185156675476458) {
   out_3295185156675476458[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3295185156675476458[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3295185156675476458[2] = 0;
   out_3295185156675476458[3] = 0;
   out_3295185156675476458[4] = 0;
   out_3295185156675476458[5] = 0;
   out_3295185156675476458[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3295185156675476458[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3295185156675476458[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3295185156675476458[9] = 0;
   out_3295185156675476458[10] = 0;
   out_3295185156675476458[11] = 0;
   out_3295185156675476458[12] = 0;
   out_3295185156675476458[13] = 0;
   out_3295185156675476458[14] = 0;
   out_3295185156675476458[15] = 0;
   out_3295185156675476458[16] = 0;
   out_3295185156675476458[17] = 0;
   out_3295185156675476458[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3295185156675476458[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3295185156675476458[20] = 0;
   out_3295185156675476458[21] = 0;
   out_3295185156675476458[22] = 0;
   out_3295185156675476458[23] = 0;
   out_3295185156675476458[24] = 0;
   out_3295185156675476458[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3295185156675476458[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3295185156675476458[27] = 0;
   out_3295185156675476458[28] = 0;
   out_3295185156675476458[29] = 0;
   out_3295185156675476458[30] = 0;
   out_3295185156675476458[31] = 0;
   out_3295185156675476458[32] = 0;
   out_3295185156675476458[33] = 0;
   out_3295185156675476458[34] = 0;
   out_3295185156675476458[35] = 0;
   out_3295185156675476458[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3295185156675476458[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3295185156675476458[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3295185156675476458[39] = 0;
   out_3295185156675476458[40] = 0;
   out_3295185156675476458[41] = 0;
   out_3295185156675476458[42] = 0;
   out_3295185156675476458[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3295185156675476458[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3295185156675476458[45] = 0;
   out_3295185156675476458[46] = 0;
   out_3295185156675476458[47] = 0;
   out_3295185156675476458[48] = 0;
   out_3295185156675476458[49] = 0;
   out_3295185156675476458[50] = 0;
   out_3295185156675476458[51] = 0;
   out_3295185156675476458[52] = 0;
   out_3295185156675476458[53] = 0;
   out_3295185156675476458[54] = 0;
   out_3295185156675476458[55] = 0;
   out_3295185156675476458[56] = 0;
   out_3295185156675476458[57] = 1;
   out_3295185156675476458[58] = 0;
   out_3295185156675476458[59] = 0;
   out_3295185156675476458[60] = 0;
   out_3295185156675476458[61] = 0;
   out_3295185156675476458[62] = 0;
   out_3295185156675476458[63] = 0;
   out_3295185156675476458[64] = 0;
   out_3295185156675476458[65] = 0;
   out_3295185156675476458[66] = dt;
   out_3295185156675476458[67] = 0;
   out_3295185156675476458[68] = 0;
   out_3295185156675476458[69] = 0;
   out_3295185156675476458[70] = 0;
   out_3295185156675476458[71] = 0;
   out_3295185156675476458[72] = 0;
   out_3295185156675476458[73] = 0;
   out_3295185156675476458[74] = 0;
   out_3295185156675476458[75] = 0;
   out_3295185156675476458[76] = 1;
   out_3295185156675476458[77] = 0;
   out_3295185156675476458[78] = 0;
   out_3295185156675476458[79] = 0;
   out_3295185156675476458[80] = 0;
   out_3295185156675476458[81] = 0;
   out_3295185156675476458[82] = 0;
   out_3295185156675476458[83] = 0;
   out_3295185156675476458[84] = 0;
   out_3295185156675476458[85] = dt;
   out_3295185156675476458[86] = 0;
   out_3295185156675476458[87] = 0;
   out_3295185156675476458[88] = 0;
   out_3295185156675476458[89] = 0;
   out_3295185156675476458[90] = 0;
   out_3295185156675476458[91] = 0;
   out_3295185156675476458[92] = 0;
   out_3295185156675476458[93] = 0;
   out_3295185156675476458[94] = 0;
   out_3295185156675476458[95] = 1;
   out_3295185156675476458[96] = 0;
   out_3295185156675476458[97] = 0;
   out_3295185156675476458[98] = 0;
   out_3295185156675476458[99] = 0;
   out_3295185156675476458[100] = 0;
   out_3295185156675476458[101] = 0;
   out_3295185156675476458[102] = 0;
   out_3295185156675476458[103] = 0;
   out_3295185156675476458[104] = dt;
   out_3295185156675476458[105] = 0;
   out_3295185156675476458[106] = 0;
   out_3295185156675476458[107] = 0;
   out_3295185156675476458[108] = 0;
   out_3295185156675476458[109] = 0;
   out_3295185156675476458[110] = 0;
   out_3295185156675476458[111] = 0;
   out_3295185156675476458[112] = 0;
   out_3295185156675476458[113] = 0;
   out_3295185156675476458[114] = 1;
   out_3295185156675476458[115] = 0;
   out_3295185156675476458[116] = 0;
   out_3295185156675476458[117] = 0;
   out_3295185156675476458[118] = 0;
   out_3295185156675476458[119] = 0;
   out_3295185156675476458[120] = 0;
   out_3295185156675476458[121] = 0;
   out_3295185156675476458[122] = 0;
   out_3295185156675476458[123] = 0;
   out_3295185156675476458[124] = 0;
   out_3295185156675476458[125] = 0;
   out_3295185156675476458[126] = 0;
   out_3295185156675476458[127] = 0;
   out_3295185156675476458[128] = 0;
   out_3295185156675476458[129] = 0;
   out_3295185156675476458[130] = 0;
   out_3295185156675476458[131] = 0;
   out_3295185156675476458[132] = 0;
   out_3295185156675476458[133] = 1;
   out_3295185156675476458[134] = 0;
   out_3295185156675476458[135] = 0;
   out_3295185156675476458[136] = 0;
   out_3295185156675476458[137] = 0;
   out_3295185156675476458[138] = 0;
   out_3295185156675476458[139] = 0;
   out_3295185156675476458[140] = 0;
   out_3295185156675476458[141] = 0;
   out_3295185156675476458[142] = 0;
   out_3295185156675476458[143] = 0;
   out_3295185156675476458[144] = 0;
   out_3295185156675476458[145] = 0;
   out_3295185156675476458[146] = 0;
   out_3295185156675476458[147] = 0;
   out_3295185156675476458[148] = 0;
   out_3295185156675476458[149] = 0;
   out_3295185156675476458[150] = 0;
   out_3295185156675476458[151] = 0;
   out_3295185156675476458[152] = 1;
   out_3295185156675476458[153] = 0;
   out_3295185156675476458[154] = 0;
   out_3295185156675476458[155] = 0;
   out_3295185156675476458[156] = 0;
   out_3295185156675476458[157] = 0;
   out_3295185156675476458[158] = 0;
   out_3295185156675476458[159] = 0;
   out_3295185156675476458[160] = 0;
   out_3295185156675476458[161] = 0;
   out_3295185156675476458[162] = 0;
   out_3295185156675476458[163] = 0;
   out_3295185156675476458[164] = 0;
   out_3295185156675476458[165] = 0;
   out_3295185156675476458[166] = 0;
   out_3295185156675476458[167] = 0;
   out_3295185156675476458[168] = 0;
   out_3295185156675476458[169] = 0;
   out_3295185156675476458[170] = 0;
   out_3295185156675476458[171] = 1;
   out_3295185156675476458[172] = 0;
   out_3295185156675476458[173] = 0;
   out_3295185156675476458[174] = 0;
   out_3295185156675476458[175] = 0;
   out_3295185156675476458[176] = 0;
   out_3295185156675476458[177] = 0;
   out_3295185156675476458[178] = 0;
   out_3295185156675476458[179] = 0;
   out_3295185156675476458[180] = 0;
   out_3295185156675476458[181] = 0;
   out_3295185156675476458[182] = 0;
   out_3295185156675476458[183] = 0;
   out_3295185156675476458[184] = 0;
   out_3295185156675476458[185] = 0;
   out_3295185156675476458[186] = 0;
   out_3295185156675476458[187] = 0;
   out_3295185156675476458[188] = 0;
   out_3295185156675476458[189] = 0;
   out_3295185156675476458[190] = 1;
   out_3295185156675476458[191] = 0;
   out_3295185156675476458[192] = 0;
   out_3295185156675476458[193] = 0;
   out_3295185156675476458[194] = 0;
   out_3295185156675476458[195] = 0;
   out_3295185156675476458[196] = 0;
   out_3295185156675476458[197] = 0;
   out_3295185156675476458[198] = 0;
   out_3295185156675476458[199] = 0;
   out_3295185156675476458[200] = 0;
   out_3295185156675476458[201] = 0;
   out_3295185156675476458[202] = 0;
   out_3295185156675476458[203] = 0;
   out_3295185156675476458[204] = 0;
   out_3295185156675476458[205] = 0;
   out_3295185156675476458[206] = 0;
   out_3295185156675476458[207] = 0;
   out_3295185156675476458[208] = 0;
   out_3295185156675476458[209] = 1;
   out_3295185156675476458[210] = 0;
   out_3295185156675476458[211] = 0;
   out_3295185156675476458[212] = 0;
   out_3295185156675476458[213] = 0;
   out_3295185156675476458[214] = 0;
   out_3295185156675476458[215] = 0;
   out_3295185156675476458[216] = 0;
   out_3295185156675476458[217] = 0;
   out_3295185156675476458[218] = 0;
   out_3295185156675476458[219] = 0;
   out_3295185156675476458[220] = 0;
   out_3295185156675476458[221] = 0;
   out_3295185156675476458[222] = 0;
   out_3295185156675476458[223] = 0;
   out_3295185156675476458[224] = 0;
   out_3295185156675476458[225] = 0;
   out_3295185156675476458[226] = 0;
   out_3295185156675476458[227] = 0;
   out_3295185156675476458[228] = 1;
   out_3295185156675476458[229] = 0;
   out_3295185156675476458[230] = 0;
   out_3295185156675476458[231] = 0;
   out_3295185156675476458[232] = 0;
   out_3295185156675476458[233] = 0;
   out_3295185156675476458[234] = 0;
   out_3295185156675476458[235] = 0;
   out_3295185156675476458[236] = 0;
   out_3295185156675476458[237] = 0;
   out_3295185156675476458[238] = 0;
   out_3295185156675476458[239] = 0;
   out_3295185156675476458[240] = 0;
   out_3295185156675476458[241] = 0;
   out_3295185156675476458[242] = 0;
   out_3295185156675476458[243] = 0;
   out_3295185156675476458[244] = 0;
   out_3295185156675476458[245] = 0;
   out_3295185156675476458[246] = 0;
   out_3295185156675476458[247] = 1;
   out_3295185156675476458[248] = 0;
   out_3295185156675476458[249] = 0;
   out_3295185156675476458[250] = 0;
   out_3295185156675476458[251] = 0;
   out_3295185156675476458[252] = 0;
   out_3295185156675476458[253] = 0;
   out_3295185156675476458[254] = 0;
   out_3295185156675476458[255] = 0;
   out_3295185156675476458[256] = 0;
   out_3295185156675476458[257] = 0;
   out_3295185156675476458[258] = 0;
   out_3295185156675476458[259] = 0;
   out_3295185156675476458[260] = 0;
   out_3295185156675476458[261] = 0;
   out_3295185156675476458[262] = 0;
   out_3295185156675476458[263] = 0;
   out_3295185156675476458[264] = 0;
   out_3295185156675476458[265] = 0;
   out_3295185156675476458[266] = 1;
   out_3295185156675476458[267] = 0;
   out_3295185156675476458[268] = 0;
   out_3295185156675476458[269] = 0;
   out_3295185156675476458[270] = 0;
   out_3295185156675476458[271] = 0;
   out_3295185156675476458[272] = 0;
   out_3295185156675476458[273] = 0;
   out_3295185156675476458[274] = 0;
   out_3295185156675476458[275] = 0;
   out_3295185156675476458[276] = 0;
   out_3295185156675476458[277] = 0;
   out_3295185156675476458[278] = 0;
   out_3295185156675476458[279] = 0;
   out_3295185156675476458[280] = 0;
   out_3295185156675476458[281] = 0;
   out_3295185156675476458[282] = 0;
   out_3295185156675476458[283] = 0;
   out_3295185156675476458[284] = 0;
   out_3295185156675476458[285] = 1;
   out_3295185156675476458[286] = 0;
   out_3295185156675476458[287] = 0;
   out_3295185156675476458[288] = 0;
   out_3295185156675476458[289] = 0;
   out_3295185156675476458[290] = 0;
   out_3295185156675476458[291] = 0;
   out_3295185156675476458[292] = 0;
   out_3295185156675476458[293] = 0;
   out_3295185156675476458[294] = 0;
   out_3295185156675476458[295] = 0;
   out_3295185156675476458[296] = 0;
   out_3295185156675476458[297] = 0;
   out_3295185156675476458[298] = 0;
   out_3295185156675476458[299] = 0;
   out_3295185156675476458[300] = 0;
   out_3295185156675476458[301] = 0;
   out_3295185156675476458[302] = 0;
   out_3295185156675476458[303] = 0;
   out_3295185156675476458[304] = 1;
   out_3295185156675476458[305] = 0;
   out_3295185156675476458[306] = 0;
   out_3295185156675476458[307] = 0;
   out_3295185156675476458[308] = 0;
   out_3295185156675476458[309] = 0;
   out_3295185156675476458[310] = 0;
   out_3295185156675476458[311] = 0;
   out_3295185156675476458[312] = 0;
   out_3295185156675476458[313] = 0;
   out_3295185156675476458[314] = 0;
   out_3295185156675476458[315] = 0;
   out_3295185156675476458[316] = 0;
   out_3295185156675476458[317] = 0;
   out_3295185156675476458[318] = 0;
   out_3295185156675476458[319] = 0;
   out_3295185156675476458[320] = 0;
   out_3295185156675476458[321] = 0;
   out_3295185156675476458[322] = 0;
   out_3295185156675476458[323] = 1;
}
void h_4(double *state, double *unused, double *out_8502160873985878055) {
   out_8502160873985878055[0] = state[6] + state[9];
   out_8502160873985878055[1] = state[7] + state[10];
   out_8502160873985878055[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_435189256188821596) {
   out_435189256188821596[0] = 0;
   out_435189256188821596[1] = 0;
   out_435189256188821596[2] = 0;
   out_435189256188821596[3] = 0;
   out_435189256188821596[4] = 0;
   out_435189256188821596[5] = 0;
   out_435189256188821596[6] = 1;
   out_435189256188821596[7] = 0;
   out_435189256188821596[8] = 0;
   out_435189256188821596[9] = 1;
   out_435189256188821596[10] = 0;
   out_435189256188821596[11] = 0;
   out_435189256188821596[12] = 0;
   out_435189256188821596[13] = 0;
   out_435189256188821596[14] = 0;
   out_435189256188821596[15] = 0;
   out_435189256188821596[16] = 0;
   out_435189256188821596[17] = 0;
   out_435189256188821596[18] = 0;
   out_435189256188821596[19] = 0;
   out_435189256188821596[20] = 0;
   out_435189256188821596[21] = 0;
   out_435189256188821596[22] = 0;
   out_435189256188821596[23] = 0;
   out_435189256188821596[24] = 0;
   out_435189256188821596[25] = 1;
   out_435189256188821596[26] = 0;
   out_435189256188821596[27] = 0;
   out_435189256188821596[28] = 1;
   out_435189256188821596[29] = 0;
   out_435189256188821596[30] = 0;
   out_435189256188821596[31] = 0;
   out_435189256188821596[32] = 0;
   out_435189256188821596[33] = 0;
   out_435189256188821596[34] = 0;
   out_435189256188821596[35] = 0;
   out_435189256188821596[36] = 0;
   out_435189256188821596[37] = 0;
   out_435189256188821596[38] = 0;
   out_435189256188821596[39] = 0;
   out_435189256188821596[40] = 0;
   out_435189256188821596[41] = 0;
   out_435189256188821596[42] = 0;
   out_435189256188821596[43] = 0;
   out_435189256188821596[44] = 1;
   out_435189256188821596[45] = 0;
   out_435189256188821596[46] = 0;
   out_435189256188821596[47] = 1;
   out_435189256188821596[48] = 0;
   out_435189256188821596[49] = 0;
   out_435189256188821596[50] = 0;
   out_435189256188821596[51] = 0;
   out_435189256188821596[52] = 0;
   out_435189256188821596[53] = 0;
}
void h_10(double *state, double *unused, double *out_9110483139119498421) {
   out_9110483139119498421[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_9110483139119498421[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_9110483139119498421[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_2767835144174802438) {
   out_2767835144174802438[0] = 0;
   out_2767835144174802438[1] = 9.8100000000000005*cos(state[1]);
   out_2767835144174802438[2] = 0;
   out_2767835144174802438[3] = 0;
   out_2767835144174802438[4] = -state[8];
   out_2767835144174802438[5] = state[7];
   out_2767835144174802438[6] = 0;
   out_2767835144174802438[7] = state[5];
   out_2767835144174802438[8] = -state[4];
   out_2767835144174802438[9] = 0;
   out_2767835144174802438[10] = 0;
   out_2767835144174802438[11] = 0;
   out_2767835144174802438[12] = 1;
   out_2767835144174802438[13] = 0;
   out_2767835144174802438[14] = 0;
   out_2767835144174802438[15] = 1;
   out_2767835144174802438[16] = 0;
   out_2767835144174802438[17] = 0;
   out_2767835144174802438[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_2767835144174802438[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_2767835144174802438[20] = 0;
   out_2767835144174802438[21] = state[8];
   out_2767835144174802438[22] = 0;
   out_2767835144174802438[23] = -state[6];
   out_2767835144174802438[24] = -state[5];
   out_2767835144174802438[25] = 0;
   out_2767835144174802438[26] = state[3];
   out_2767835144174802438[27] = 0;
   out_2767835144174802438[28] = 0;
   out_2767835144174802438[29] = 0;
   out_2767835144174802438[30] = 0;
   out_2767835144174802438[31] = 1;
   out_2767835144174802438[32] = 0;
   out_2767835144174802438[33] = 0;
   out_2767835144174802438[34] = 1;
   out_2767835144174802438[35] = 0;
   out_2767835144174802438[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_2767835144174802438[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_2767835144174802438[38] = 0;
   out_2767835144174802438[39] = -state[7];
   out_2767835144174802438[40] = state[6];
   out_2767835144174802438[41] = 0;
   out_2767835144174802438[42] = state[4];
   out_2767835144174802438[43] = -state[3];
   out_2767835144174802438[44] = 0;
   out_2767835144174802438[45] = 0;
   out_2767835144174802438[46] = 0;
   out_2767835144174802438[47] = 0;
   out_2767835144174802438[48] = 0;
   out_2767835144174802438[49] = 0;
   out_2767835144174802438[50] = 1;
   out_2767835144174802438[51] = 0;
   out_2767835144174802438[52] = 0;
   out_2767835144174802438[53] = 1;
}
void h_13(double *state, double *unused, double *out_4484268190362306043) {
   out_4484268190362306043[0] = state[3];
   out_4484268190362306043[1] = state[4];
   out_4484268190362306043[2] = state[5];
}
void H_13(double *state, double *unused, double *out_3647463081521154397) {
   out_3647463081521154397[0] = 0;
   out_3647463081521154397[1] = 0;
   out_3647463081521154397[2] = 0;
   out_3647463081521154397[3] = 1;
   out_3647463081521154397[4] = 0;
   out_3647463081521154397[5] = 0;
   out_3647463081521154397[6] = 0;
   out_3647463081521154397[7] = 0;
   out_3647463081521154397[8] = 0;
   out_3647463081521154397[9] = 0;
   out_3647463081521154397[10] = 0;
   out_3647463081521154397[11] = 0;
   out_3647463081521154397[12] = 0;
   out_3647463081521154397[13] = 0;
   out_3647463081521154397[14] = 0;
   out_3647463081521154397[15] = 0;
   out_3647463081521154397[16] = 0;
   out_3647463081521154397[17] = 0;
   out_3647463081521154397[18] = 0;
   out_3647463081521154397[19] = 0;
   out_3647463081521154397[20] = 0;
   out_3647463081521154397[21] = 0;
   out_3647463081521154397[22] = 1;
   out_3647463081521154397[23] = 0;
   out_3647463081521154397[24] = 0;
   out_3647463081521154397[25] = 0;
   out_3647463081521154397[26] = 0;
   out_3647463081521154397[27] = 0;
   out_3647463081521154397[28] = 0;
   out_3647463081521154397[29] = 0;
   out_3647463081521154397[30] = 0;
   out_3647463081521154397[31] = 0;
   out_3647463081521154397[32] = 0;
   out_3647463081521154397[33] = 0;
   out_3647463081521154397[34] = 0;
   out_3647463081521154397[35] = 0;
   out_3647463081521154397[36] = 0;
   out_3647463081521154397[37] = 0;
   out_3647463081521154397[38] = 0;
   out_3647463081521154397[39] = 0;
   out_3647463081521154397[40] = 0;
   out_3647463081521154397[41] = 1;
   out_3647463081521154397[42] = 0;
   out_3647463081521154397[43] = 0;
   out_3647463081521154397[44] = 0;
   out_3647463081521154397[45] = 0;
   out_3647463081521154397[46] = 0;
   out_3647463081521154397[47] = 0;
   out_3647463081521154397[48] = 0;
   out_3647463081521154397[49] = 0;
   out_3647463081521154397[50] = 0;
   out_3647463081521154397[51] = 0;
   out_3647463081521154397[52] = 0;
   out_3647463081521154397[53] = 0;
}
void h_14(double *state, double *unused, double *out_378511576534692133) {
   out_378511576534692133[0] = state[6];
   out_378511576534692133[1] = state[7];
   out_378511576534692133[2] = state[8];
}
void H_14(double *state, double *unused, double *out_2647599176106550700) {
   out_2647599176106550700[0] = 0;
   out_2647599176106550700[1] = 0;
   out_2647599176106550700[2] = 0;
   out_2647599176106550700[3] = 0;
   out_2647599176106550700[4] = 0;
   out_2647599176106550700[5] = 0;
   out_2647599176106550700[6] = 1;
   out_2647599176106550700[7] = 0;
   out_2647599176106550700[8] = 0;
   out_2647599176106550700[9] = 0;
   out_2647599176106550700[10] = 0;
   out_2647599176106550700[11] = 0;
   out_2647599176106550700[12] = 0;
   out_2647599176106550700[13] = 0;
   out_2647599176106550700[14] = 0;
   out_2647599176106550700[15] = 0;
   out_2647599176106550700[16] = 0;
   out_2647599176106550700[17] = 0;
   out_2647599176106550700[18] = 0;
   out_2647599176106550700[19] = 0;
   out_2647599176106550700[20] = 0;
   out_2647599176106550700[21] = 0;
   out_2647599176106550700[22] = 0;
   out_2647599176106550700[23] = 0;
   out_2647599176106550700[24] = 0;
   out_2647599176106550700[25] = 1;
   out_2647599176106550700[26] = 0;
   out_2647599176106550700[27] = 0;
   out_2647599176106550700[28] = 0;
   out_2647599176106550700[29] = 0;
   out_2647599176106550700[30] = 0;
   out_2647599176106550700[31] = 0;
   out_2647599176106550700[32] = 0;
   out_2647599176106550700[33] = 0;
   out_2647599176106550700[34] = 0;
   out_2647599176106550700[35] = 0;
   out_2647599176106550700[36] = 0;
   out_2647599176106550700[37] = 0;
   out_2647599176106550700[38] = 0;
   out_2647599176106550700[39] = 0;
   out_2647599176106550700[40] = 0;
   out_2647599176106550700[41] = 0;
   out_2647599176106550700[42] = 0;
   out_2647599176106550700[43] = 0;
   out_2647599176106550700[44] = 1;
   out_2647599176106550700[45] = 0;
   out_2647599176106550700[46] = 0;
   out_2647599176106550700[47] = 0;
   out_2647599176106550700[48] = 0;
   out_2647599176106550700[49] = 0;
   out_2647599176106550700[50] = 0;
   out_2647599176106550700[51] = 0;
   out_2647599176106550700[52] = 0;
   out_2647599176106550700[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_8879109673270157512) {
  err_fun(nom_x, delta_x, out_8879109673270157512);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_341167623382481477) {
  inv_err_fun(nom_x, true_x, out_341167623382481477);
}
void pose_H_mod_fun(double *state, double *out_3336617953655085976) {
  H_mod_fun(state, out_3336617953655085976);
}
void pose_f_fun(double *state, double dt, double *out_8832833102796243366) {
  f_fun(state,  dt, out_8832833102796243366);
}
void pose_F_fun(double *state, double dt, double *out_3295185156675476458) {
  F_fun(state,  dt, out_3295185156675476458);
}
void pose_h_4(double *state, double *unused, double *out_8502160873985878055) {
  h_4(state, unused, out_8502160873985878055);
}
void pose_H_4(double *state, double *unused, double *out_435189256188821596) {
  H_4(state, unused, out_435189256188821596);
}
void pose_h_10(double *state, double *unused, double *out_9110483139119498421) {
  h_10(state, unused, out_9110483139119498421);
}
void pose_H_10(double *state, double *unused, double *out_2767835144174802438) {
  H_10(state, unused, out_2767835144174802438);
}
void pose_h_13(double *state, double *unused, double *out_4484268190362306043) {
  h_13(state, unused, out_4484268190362306043);
}
void pose_H_13(double *state, double *unused, double *out_3647463081521154397) {
  H_13(state, unused, out_3647463081521154397);
}
void pose_h_14(double *state, double *unused, double *out_378511576534692133) {
  h_14(state, unused, out_378511576534692133);
}
void pose_H_14(double *state, double *unused, double *out_2647599176106550700) {
  H_14(state, unused, out_2647599176106550700);
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
