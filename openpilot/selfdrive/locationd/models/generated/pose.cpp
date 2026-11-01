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
void err_fun(double *nom_x, double *delta_x, double *out_2134324718959906218) {
   out_2134324718959906218[0] = delta_x[0] + nom_x[0];
   out_2134324718959906218[1] = delta_x[1] + nom_x[1];
   out_2134324718959906218[2] = delta_x[2] + nom_x[2];
   out_2134324718959906218[3] = delta_x[3] + nom_x[3];
   out_2134324718959906218[4] = delta_x[4] + nom_x[4];
   out_2134324718959906218[5] = delta_x[5] + nom_x[5];
   out_2134324718959906218[6] = delta_x[6] + nom_x[6];
   out_2134324718959906218[7] = delta_x[7] + nom_x[7];
   out_2134324718959906218[8] = delta_x[8] + nom_x[8];
   out_2134324718959906218[9] = delta_x[9] + nom_x[9];
   out_2134324718959906218[10] = delta_x[10] + nom_x[10];
   out_2134324718959906218[11] = delta_x[11] + nom_x[11];
   out_2134324718959906218[12] = delta_x[12] + nom_x[12];
   out_2134324718959906218[13] = delta_x[13] + nom_x[13];
   out_2134324718959906218[14] = delta_x[14] + nom_x[14];
   out_2134324718959906218[15] = delta_x[15] + nom_x[15];
   out_2134324718959906218[16] = delta_x[16] + nom_x[16];
   out_2134324718959906218[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_7874553077816062449) {
   out_7874553077816062449[0] = -nom_x[0] + true_x[0];
   out_7874553077816062449[1] = -nom_x[1] + true_x[1];
   out_7874553077816062449[2] = -nom_x[2] + true_x[2];
   out_7874553077816062449[3] = -nom_x[3] + true_x[3];
   out_7874553077816062449[4] = -nom_x[4] + true_x[4];
   out_7874553077816062449[5] = -nom_x[5] + true_x[5];
   out_7874553077816062449[6] = -nom_x[6] + true_x[6];
   out_7874553077816062449[7] = -nom_x[7] + true_x[7];
   out_7874553077816062449[8] = -nom_x[8] + true_x[8];
   out_7874553077816062449[9] = -nom_x[9] + true_x[9];
   out_7874553077816062449[10] = -nom_x[10] + true_x[10];
   out_7874553077816062449[11] = -nom_x[11] + true_x[11];
   out_7874553077816062449[12] = -nom_x[12] + true_x[12];
   out_7874553077816062449[13] = -nom_x[13] + true_x[13];
   out_7874553077816062449[14] = -nom_x[14] + true_x[14];
   out_7874553077816062449[15] = -nom_x[15] + true_x[15];
   out_7874553077816062449[16] = -nom_x[16] + true_x[16];
   out_7874553077816062449[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_8129875391940694982) {
   out_8129875391940694982[0] = 1.0;
   out_8129875391940694982[1] = 0.0;
   out_8129875391940694982[2] = 0.0;
   out_8129875391940694982[3] = 0.0;
   out_8129875391940694982[4] = 0.0;
   out_8129875391940694982[5] = 0.0;
   out_8129875391940694982[6] = 0.0;
   out_8129875391940694982[7] = 0.0;
   out_8129875391940694982[8] = 0.0;
   out_8129875391940694982[9] = 0.0;
   out_8129875391940694982[10] = 0.0;
   out_8129875391940694982[11] = 0.0;
   out_8129875391940694982[12] = 0.0;
   out_8129875391940694982[13] = 0.0;
   out_8129875391940694982[14] = 0.0;
   out_8129875391940694982[15] = 0.0;
   out_8129875391940694982[16] = 0.0;
   out_8129875391940694982[17] = 0.0;
   out_8129875391940694982[18] = 0.0;
   out_8129875391940694982[19] = 1.0;
   out_8129875391940694982[20] = 0.0;
   out_8129875391940694982[21] = 0.0;
   out_8129875391940694982[22] = 0.0;
   out_8129875391940694982[23] = 0.0;
   out_8129875391940694982[24] = 0.0;
   out_8129875391940694982[25] = 0.0;
   out_8129875391940694982[26] = 0.0;
   out_8129875391940694982[27] = 0.0;
   out_8129875391940694982[28] = 0.0;
   out_8129875391940694982[29] = 0.0;
   out_8129875391940694982[30] = 0.0;
   out_8129875391940694982[31] = 0.0;
   out_8129875391940694982[32] = 0.0;
   out_8129875391940694982[33] = 0.0;
   out_8129875391940694982[34] = 0.0;
   out_8129875391940694982[35] = 0.0;
   out_8129875391940694982[36] = 0.0;
   out_8129875391940694982[37] = 0.0;
   out_8129875391940694982[38] = 1.0;
   out_8129875391940694982[39] = 0.0;
   out_8129875391940694982[40] = 0.0;
   out_8129875391940694982[41] = 0.0;
   out_8129875391940694982[42] = 0.0;
   out_8129875391940694982[43] = 0.0;
   out_8129875391940694982[44] = 0.0;
   out_8129875391940694982[45] = 0.0;
   out_8129875391940694982[46] = 0.0;
   out_8129875391940694982[47] = 0.0;
   out_8129875391940694982[48] = 0.0;
   out_8129875391940694982[49] = 0.0;
   out_8129875391940694982[50] = 0.0;
   out_8129875391940694982[51] = 0.0;
   out_8129875391940694982[52] = 0.0;
   out_8129875391940694982[53] = 0.0;
   out_8129875391940694982[54] = 0.0;
   out_8129875391940694982[55] = 0.0;
   out_8129875391940694982[56] = 0.0;
   out_8129875391940694982[57] = 1.0;
   out_8129875391940694982[58] = 0.0;
   out_8129875391940694982[59] = 0.0;
   out_8129875391940694982[60] = 0.0;
   out_8129875391940694982[61] = 0.0;
   out_8129875391940694982[62] = 0.0;
   out_8129875391940694982[63] = 0.0;
   out_8129875391940694982[64] = 0.0;
   out_8129875391940694982[65] = 0.0;
   out_8129875391940694982[66] = 0.0;
   out_8129875391940694982[67] = 0.0;
   out_8129875391940694982[68] = 0.0;
   out_8129875391940694982[69] = 0.0;
   out_8129875391940694982[70] = 0.0;
   out_8129875391940694982[71] = 0.0;
   out_8129875391940694982[72] = 0.0;
   out_8129875391940694982[73] = 0.0;
   out_8129875391940694982[74] = 0.0;
   out_8129875391940694982[75] = 0.0;
   out_8129875391940694982[76] = 1.0;
   out_8129875391940694982[77] = 0.0;
   out_8129875391940694982[78] = 0.0;
   out_8129875391940694982[79] = 0.0;
   out_8129875391940694982[80] = 0.0;
   out_8129875391940694982[81] = 0.0;
   out_8129875391940694982[82] = 0.0;
   out_8129875391940694982[83] = 0.0;
   out_8129875391940694982[84] = 0.0;
   out_8129875391940694982[85] = 0.0;
   out_8129875391940694982[86] = 0.0;
   out_8129875391940694982[87] = 0.0;
   out_8129875391940694982[88] = 0.0;
   out_8129875391940694982[89] = 0.0;
   out_8129875391940694982[90] = 0.0;
   out_8129875391940694982[91] = 0.0;
   out_8129875391940694982[92] = 0.0;
   out_8129875391940694982[93] = 0.0;
   out_8129875391940694982[94] = 0.0;
   out_8129875391940694982[95] = 1.0;
   out_8129875391940694982[96] = 0.0;
   out_8129875391940694982[97] = 0.0;
   out_8129875391940694982[98] = 0.0;
   out_8129875391940694982[99] = 0.0;
   out_8129875391940694982[100] = 0.0;
   out_8129875391940694982[101] = 0.0;
   out_8129875391940694982[102] = 0.0;
   out_8129875391940694982[103] = 0.0;
   out_8129875391940694982[104] = 0.0;
   out_8129875391940694982[105] = 0.0;
   out_8129875391940694982[106] = 0.0;
   out_8129875391940694982[107] = 0.0;
   out_8129875391940694982[108] = 0.0;
   out_8129875391940694982[109] = 0.0;
   out_8129875391940694982[110] = 0.0;
   out_8129875391940694982[111] = 0.0;
   out_8129875391940694982[112] = 0.0;
   out_8129875391940694982[113] = 0.0;
   out_8129875391940694982[114] = 1.0;
   out_8129875391940694982[115] = 0.0;
   out_8129875391940694982[116] = 0.0;
   out_8129875391940694982[117] = 0.0;
   out_8129875391940694982[118] = 0.0;
   out_8129875391940694982[119] = 0.0;
   out_8129875391940694982[120] = 0.0;
   out_8129875391940694982[121] = 0.0;
   out_8129875391940694982[122] = 0.0;
   out_8129875391940694982[123] = 0.0;
   out_8129875391940694982[124] = 0.0;
   out_8129875391940694982[125] = 0.0;
   out_8129875391940694982[126] = 0.0;
   out_8129875391940694982[127] = 0.0;
   out_8129875391940694982[128] = 0.0;
   out_8129875391940694982[129] = 0.0;
   out_8129875391940694982[130] = 0.0;
   out_8129875391940694982[131] = 0.0;
   out_8129875391940694982[132] = 0.0;
   out_8129875391940694982[133] = 1.0;
   out_8129875391940694982[134] = 0.0;
   out_8129875391940694982[135] = 0.0;
   out_8129875391940694982[136] = 0.0;
   out_8129875391940694982[137] = 0.0;
   out_8129875391940694982[138] = 0.0;
   out_8129875391940694982[139] = 0.0;
   out_8129875391940694982[140] = 0.0;
   out_8129875391940694982[141] = 0.0;
   out_8129875391940694982[142] = 0.0;
   out_8129875391940694982[143] = 0.0;
   out_8129875391940694982[144] = 0.0;
   out_8129875391940694982[145] = 0.0;
   out_8129875391940694982[146] = 0.0;
   out_8129875391940694982[147] = 0.0;
   out_8129875391940694982[148] = 0.0;
   out_8129875391940694982[149] = 0.0;
   out_8129875391940694982[150] = 0.0;
   out_8129875391940694982[151] = 0.0;
   out_8129875391940694982[152] = 1.0;
   out_8129875391940694982[153] = 0.0;
   out_8129875391940694982[154] = 0.0;
   out_8129875391940694982[155] = 0.0;
   out_8129875391940694982[156] = 0.0;
   out_8129875391940694982[157] = 0.0;
   out_8129875391940694982[158] = 0.0;
   out_8129875391940694982[159] = 0.0;
   out_8129875391940694982[160] = 0.0;
   out_8129875391940694982[161] = 0.0;
   out_8129875391940694982[162] = 0.0;
   out_8129875391940694982[163] = 0.0;
   out_8129875391940694982[164] = 0.0;
   out_8129875391940694982[165] = 0.0;
   out_8129875391940694982[166] = 0.0;
   out_8129875391940694982[167] = 0.0;
   out_8129875391940694982[168] = 0.0;
   out_8129875391940694982[169] = 0.0;
   out_8129875391940694982[170] = 0.0;
   out_8129875391940694982[171] = 1.0;
   out_8129875391940694982[172] = 0.0;
   out_8129875391940694982[173] = 0.0;
   out_8129875391940694982[174] = 0.0;
   out_8129875391940694982[175] = 0.0;
   out_8129875391940694982[176] = 0.0;
   out_8129875391940694982[177] = 0.0;
   out_8129875391940694982[178] = 0.0;
   out_8129875391940694982[179] = 0.0;
   out_8129875391940694982[180] = 0.0;
   out_8129875391940694982[181] = 0.0;
   out_8129875391940694982[182] = 0.0;
   out_8129875391940694982[183] = 0.0;
   out_8129875391940694982[184] = 0.0;
   out_8129875391940694982[185] = 0.0;
   out_8129875391940694982[186] = 0.0;
   out_8129875391940694982[187] = 0.0;
   out_8129875391940694982[188] = 0.0;
   out_8129875391940694982[189] = 0.0;
   out_8129875391940694982[190] = 1.0;
   out_8129875391940694982[191] = 0.0;
   out_8129875391940694982[192] = 0.0;
   out_8129875391940694982[193] = 0.0;
   out_8129875391940694982[194] = 0.0;
   out_8129875391940694982[195] = 0.0;
   out_8129875391940694982[196] = 0.0;
   out_8129875391940694982[197] = 0.0;
   out_8129875391940694982[198] = 0.0;
   out_8129875391940694982[199] = 0.0;
   out_8129875391940694982[200] = 0.0;
   out_8129875391940694982[201] = 0.0;
   out_8129875391940694982[202] = 0.0;
   out_8129875391940694982[203] = 0.0;
   out_8129875391940694982[204] = 0.0;
   out_8129875391940694982[205] = 0.0;
   out_8129875391940694982[206] = 0.0;
   out_8129875391940694982[207] = 0.0;
   out_8129875391940694982[208] = 0.0;
   out_8129875391940694982[209] = 1.0;
   out_8129875391940694982[210] = 0.0;
   out_8129875391940694982[211] = 0.0;
   out_8129875391940694982[212] = 0.0;
   out_8129875391940694982[213] = 0.0;
   out_8129875391940694982[214] = 0.0;
   out_8129875391940694982[215] = 0.0;
   out_8129875391940694982[216] = 0.0;
   out_8129875391940694982[217] = 0.0;
   out_8129875391940694982[218] = 0.0;
   out_8129875391940694982[219] = 0.0;
   out_8129875391940694982[220] = 0.0;
   out_8129875391940694982[221] = 0.0;
   out_8129875391940694982[222] = 0.0;
   out_8129875391940694982[223] = 0.0;
   out_8129875391940694982[224] = 0.0;
   out_8129875391940694982[225] = 0.0;
   out_8129875391940694982[226] = 0.0;
   out_8129875391940694982[227] = 0.0;
   out_8129875391940694982[228] = 1.0;
   out_8129875391940694982[229] = 0.0;
   out_8129875391940694982[230] = 0.0;
   out_8129875391940694982[231] = 0.0;
   out_8129875391940694982[232] = 0.0;
   out_8129875391940694982[233] = 0.0;
   out_8129875391940694982[234] = 0.0;
   out_8129875391940694982[235] = 0.0;
   out_8129875391940694982[236] = 0.0;
   out_8129875391940694982[237] = 0.0;
   out_8129875391940694982[238] = 0.0;
   out_8129875391940694982[239] = 0.0;
   out_8129875391940694982[240] = 0.0;
   out_8129875391940694982[241] = 0.0;
   out_8129875391940694982[242] = 0.0;
   out_8129875391940694982[243] = 0.0;
   out_8129875391940694982[244] = 0.0;
   out_8129875391940694982[245] = 0.0;
   out_8129875391940694982[246] = 0.0;
   out_8129875391940694982[247] = 1.0;
   out_8129875391940694982[248] = 0.0;
   out_8129875391940694982[249] = 0.0;
   out_8129875391940694982[250] = 0.0;
   out_8129875391940694982[251] = 0.0;
   out_8129875391940694982[252] = 0.0;
   out_8129875391940694982[253] = 0.0;
   out_8129875391940694982[254] = 0.0;
   out_8129875391940694982[255] = 0.0;
   out_8129875391940694982[256] = 0.0;
   out_8129875391940694982[257] = 0.0;
   out_8129875391940694982[258] = 0.0;
   out_8129875391940694982[259] = 0.0;
   out_8129875391940694982[260] = 0.0;
   out_8129875391940694982[261] = 0.0;
   out_8129875391940694982[262] = 0.0;
   out_8129875391940694982[263] = 0.0;
   out_8129875391940694982[264] = 0.0;
   out_8129875391940694982[265] = 0.0;
   out_8129875391940694982[266] = 1.0;
   out_8129875391940694982[267] = 0.0;
   out_8129875391940694982[268] = 0.0;
   out_8129875391940694982[269] = 0.0;
   out_8129875391940694982[270] = 0.0;
   out_8129875391940694982[271] = 0.0;
   out_8129875391940694982[272] = 0.0;
   out_8129875391940694982[273] = 0.0;
   out_8129875391940694982[274] = 0.0;
   out_8129875391940694982[275] = 0.0;
   out_8129875391940694982[276] = 0.0;
   out_8129875391940694982[277] = 0.0;
   out_8129875391940694982[278] = 0.0;
   out_8129875391940694982[279] = 0.0;
   out_8129875391940694982[280] = 0.0;
   out_8129875391940694982[281] = 0.0;
   out_8129875391940694982[282] = 0.0;
   out_8129875391940694982[283] = 0.0;
   out_8129875391940694982[284] = 0.0;
   out_8129875391940694982[285] = 1.0;
   out_8129875391940694982[286] = 0.0;
   out_8129875391940694982[287] = 0.0;
   out_8129875391940694982[288] = 0.0;
   out_8129875391940694982[289] = 0.0;
   out_8129875391940694982[290] = 0.0;
   out_8129875391940694982[291] = 0.0;
   out_8129875391940694982[292] = 0.0;
   out_8129875391940694982[293] = 0.0;
   out_8129875391940694982[294] = 0.0;
   out_8129875391940694982[295] = 0.0;
   out_8129875391940694982[296] = 0.0;
   out_8129875391940694982[297] = 0.0;
   out_8129875391940694982[298] = 0.0;
   out_8129875391940694982[299] = 0.0;
   out_8129875391940694982[300] = 0.0;
   out_8129875391940694982[301] = 0.0;
   out_8129875391940694982[302] = 0.0;
   out_8129875391940694982[303] = 0.0;
   out_8129875391940694982[304] = 1.0;
   out_8129875391940694982[305] = 0.0;
   out_8129875391940694982[306] = 0.0;
   out_8129875391940694982[307] = 0.0;
   out_8129875391940694982[308] = 0.0;
   out_8129875391940694982[309] = 0.0;
   out_8129875391940694982[310] = 0.0;
   out_8129875391940694982[311] = 0.0;
   out_8129875391940694982[312] = 0.0;
   out_8129875391940694982[313] = 0.0;
   out_8129875391940694982[314] = 0.0;
   out_8129875391940694982[315] = 0.0;
   out_8129875391940694982[316] = 0.0;
   out_8129875391940694982[317] = 0.0;
   out_8129875391940694982[318] = 0.0;
   out_8129875391940694982[319] = 0.0;
   out_8129875391940694982[320] = 0.0;
   out_8129875391940694982[321] = 0.0;
   out_8129875391940694982[322] = 0.0;
   out_8129875391940694982[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_5920058179701921818) {
   out_5920058179701921818[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_5920058179701921818[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_5920058179701921818[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_5920058179701921818[3] = dt*state[12] + state[3];
   out_5920058179701921818[4] = dt*state[13] + state[4];
   out_5920058179701921818[5] = dt*state[14] + state[5];
   out_5920058179701921818[6] = state[6];
   out_5920058179701921818[7] = state[7];
   out_5920058179701921818[8] = state[8];
   out_5920058179701921818[9] = state[9];
   out_5920058179701921818[10] = state[10];
   out_5920058179701921818[11] = state[11];
   out_5920058179701921818[12] = state[12];
   out_5920058179701921818[13] = state[13];
   out_5920058179701921818[14] = state[14];
   out_5920058179701921818[15] = state[15];
   out_5920058179701921818[16] = state[16];
   out_5920058179701921818[17] = state[17];
}
void F_fun(double *state, double dt, double *out_534012189001621552) {
   out_534012189001621552[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_534012189001621552[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_534012189001621552[2] = 0;
   out_534012189001621552[3] = 0;
   out_534012189001621552[4] = 0;
   out_534012189001621552[5] = 0;
   out_534012189001621552[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_534012189001621552[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_534012189001621552[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_534012189001621552[9] = 0;
   out_534012189001621552[10] = 0;
   out_534012189001621552[11] = 0;
   out_534012189001621552[12] = 0;
   out_534012189001621552[13] = 0;
   out_534012189001621552[14] = 0;
   out_534012189001621552[15] = 0;
   out_534012189001621552[16] = 0;
   out_534012189001621552[17] = 0;
   out_534012189001621552[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_534012189001621552[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_534012189001621552[20] = 0;
   out_534012189001621552[21] = 0;
   out_534012189001621552[22] = 0;
   out_534012189001621552[23] = 0;
   out_534012189001621552[24] = 0;
   out_534012189001621552[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_534012189001621552[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_534012189001621552[27] = 0;
   out_534012189001621552[28] = 0;
   out_534012189001621552[29] = 0;
   out_534012189001621552[30] = 0;
   out_534012189001621552[31] = 0;
   out_534012189001621552[32] = 0;
   out_534012189001621552[33] = 0;
   out_534012189001621552[34] = 0;
   out_534012189001621552[35] = 0;
   out_534012189001621552[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_534012189001621552[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_534012189001621552[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_534012189001621552[39] = 0;
   out_534012189001621552[40] = 0;
   out_534012189001621552[41] = 0;
   out_534012189001621552[42] = 0;
   out_534012189001621552[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_534012189001621552[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_534012189001621552[45] = 0;
   out_534012189001621552[46] = 0;
   out_534012189001621552[47] = 0;
   out_534012189001621552[48] = 0;
   out_534012189001621552[49] = 0;
   out_534012189001621552[50] = 0;
   out_534012189001621552[51] = 0;
   out_534012189001621552[52] = 0;
   out_534012189001621552[53] = 0;
   out_534012189001621552[54] = 0;
   out_534012189001621552[55] = 0;
   out_534012189001621552[56] = 0;
   out_534012189001621552[57] = 1;
   out_534012189001621552[58] = 0;
   out_534012189001621552[59] = 0;
   out_534012189001621552[60] = 0;
   out_534012189001621552[61] = 0;
   out_534012189001621552[62] = 0;
   out_534012189001621552[63] = 0;
   out_534012189001621552[64] = 0;
   out_534012189001621552[65] = 0;
   out_534012189001621552[66] = dt;
   out_534012189001621552[67] = 0;
   out_534012189001621552[68] = 0;
   out_534012189001621552[69] = 0;
   out_534012189001621552[70] = 0;
   out_534012189001621552[71] = 0;
   out_534012189001621552[72] = 0;
   out_534012189001621552[73] = 0;
   out_534012189001621552[74] = 0;
   out_534012189001621552[75] = 0;
   out_534012189001621552[76] = 1;
   out_534012189001621552[77] = 0;
   out_534012189001621552[78] = 0;
   out_534012189001621552[79] = 0;
   out_534012189001621552[80] = 0;
   out_534012189001621552[81] = 0;
   out_534012189001621552[82] = 0;
   out_534012189001621552[83] = 0;
   out_534012189001621552[84] = 0;
   out_534012189001621552[85] = dt;
   out_534012189001621552[86] = 0;
   out_534012189001621552[87] = 0;
   out_534012189001621552[88] = 0;
   out_534012189001621552[89] = 0;
   out_534012189001621552[90] = 0;
   out_534012189001621552[91] = 0;
   out_534012189001621552[92] = 0;
   out_534012189001621552[93] = 0;
   out_534012189001621552[94] = 0;
   out_534012189001621552[95] = 1;
   out_534012189001621552[96] = 0;
   out_534012189001621552[97] = 0;
   out_534012189001621552[98] = 0;
   out_534012189001621552[99] = 0;
   out_534012189001621552[100] = 0;
   out_534012189001621552[101] = 0;
   out_534012189001621552[102] = 0;
   out_534012189001621552[103] = 0;
   out_534012189001621552[104] = dt;
   out_534012189001621552[105] = 0;
   out_534012189001621552[106] = 0;
   out_534012189001621552[107] = 0;
   out_534012189001621552[108] = 0;
   out_534012189001621552[109] = 0;
   out_534012189001621552[110] = 0;
   out_534012189001621552[111] = 0;
   out_534012189001621552[112] = 0;
   out_534012189001621552[113] = 0;
   out_534012189001621552[114] = 1;
   out_534012189001621552[115] = 0;
   out_534012189001621552[116] = 0;
   out_534012189001621552[117] = 0;
   out_534012189001621552[118] = 0;
   out_534012189001621552[119] = 0;
   out_534012189001621552[120] = 0;
   out_534012189001621552[121] = 0;
   out_534012189001621552[122] = 0;
   out_534012189001621552[123] = 0;
   out_534012189001621552[124] = 0;
   out_534012189001621552[125] = 0;
   out_534012189001621552[126] = 0;
   out_534012189001621552[127] = 0;
   out_534012189001621552[128] = 0;
   out_534012189001621552[129] = 0;
   out_534012189001621552[130] = 0;
   out_534012189001621552[131] = 0;
   out_534012189001621552[132] = 0;
   out_534012189001621552[133] = 1;
   out_534012189001621552[134] = 0;
   out_534012189001621552[135] = 0;
   out_534012189001621552[136] = 0;
   out_534012189001621552[137] = 0;
   out_534012189001621552[138] = 0;
   out_534012189001621552[139] = 0;
   out_534012189001621552[140] = 0;
   out_534012189001621552[141] = 0;
   out_534012189001621552[142] = 0;
   out_534012189001621552[143] = 0;
   out_534012189001621552[144] = 0;
   out_534012189001621552[145] = 0;
   out_534012189001621552[146] = 0;
   out_534012189001621552[147] = 0;
   out_534012189001621552[148] = 0;
   out_534012189001621552[149] = 0;
   out_534012189001621552[150] = 0;
   out_534012189001621552[151] = 0;
   out_534012189001621552[152] = 1;
   out_534012189001621552[153] = 0;
   out_534012189001621552[154] = 0;
   out_534012189001621552[155] = 0;
   out_534012189001621552[156] = 0;
   out_534012189001621552[157] = 0;
   out_534012189001621552[158] = 0;
   out_534012189001621552[159] = 0;
   out_534012189001621552[160] = 0;
   out_534012189001621552[161] = 0;
   out_534012189001621552[162] = 0;
   out_534012189001621552[163] = 0;
   out_534012189001621552[164] = 0;
   out_534012189001621552[165] = 0;
   out_534012189001621552[166] = 0;
   out_534012189001621552[167] = 0;
   out_534012189001621552[168] = 0;
   out_534012189001621552[169] = 0;
   out_534012189001621552[170] = 0;
   out_534012189001621552[171] = 1;
   out_534012189001621552[172] = 0;
   out_534012189001621552[173] = 0;
   out_534012189001621552[174] = 0;
   out_534012189001621552[175] = 0;
   out_534012189001621552[176] = 0;
   out_534012189001621552[177] = 0;
   out_534012189001621552[178] = 0;
   out_534012189001621552[179] = 0;
   out_534012189001621552[180] = 0;
   out_534012189001621552[181] = 0;
   out_534012189001621552[182] = 0;
   out_534012189001621552[183] = 0;
   out_534012189001621552[184] = 0;
   out_534012189001621552[185] = 0;
   out_534012189001621552[186] = 0;
   out_534012189001621552[187] = 0;
   out_534012189001621552[188] = 0;
   out_534012189001621552[189] = 0;
   out_534012189001621552[190] = 1;
   out_534012189001621552[191] = 0;
   out_534012189001621552[192] = 0;
   out_534012189001621552[193] = 0;
   out_534012189001621552[194] = 0;
   out_534012189001621552[195] = 0;
   out_534012189001621552[196] = 0;
   out_534012189001621552[197] = 0;
   out_534012189001621552[198] = 0;
   out_534012189001621552[199] = 0;
   out_534012189001621552[200] = 0;
   out_534012189001621552[201] = 0;
   out_534012189001621552[202] = 0;
   out_534012189001621552[203] = 0;
   out_534012189001621552[204] = 0;
   out_534012189001621552[205] = 0;
   out_534012189001621552[206] = 0;
   out_534012189001621552[207] = 0;
   out_534012189001621552[208] = 0;
   out_534012189001621552[209] = 1;
   out_534012189001621552[210] = 0;
   out_534012189001621552[211] = 0;
   out_534012189001621552[212] = 0;
   out_534012189001621552[213] = 0;
   out_534012189001621552[214] = 0;
   out_534012189001621552[215] = 0;
   out_534012189001621552[216] = 0;
   out_534012189001621552[217] = 0;
   out_534012189001621552[218] = 0;
   out_534012189001621552[219] = 0;
   out_534012189001621552[220] = 0;
   out_534012189001621552[221] = 0;
   out_534012189001621552[222] = 0;
   out_534012189001621552[223] = 0;
   out_534012189001621552[224] = 0;
   out_534012189001621552[225] = 0;
   out_534012189001621552[226] = 0;
   out_534012189001621552[227] = 0;
   out_534012189001621552[228] = 1;
   out_534012189001621552[229] = 0;
   out_534012189001621552[230] = 0;
   out_534012189001621552[231] = 0;
   out_534012189001621552[232] = 0;
   out_534012189001621552[233] = 0;
   out_534012189001621552[234] = 0;
   out_534012189001621552[235] = 0;
   out_534012189001621552[236] = 0;
   out_534012189001621552[237] = 0;
   out_534012189001621552[238] = 0;
   out_534012189001621552[239] = 0;
   out_534012189001621552[240] = 0;
   out_534012189001621552[241] = 0;
   out_534012189001621552[242] = 0;
   out_534012189001621552[243] = 0;
   out_534012189001621552[244] = 0;
   out_534012189001621552[245] = 0;
   out_534012189001621552[246] = 0;
   out_534012189001621552[247] = 1;
   out_534012189001621552[248] = 0;
   out_534012189001621552[249] = 0;
   out_534012189001621552[250] = 0;
   out_534012189001621552[251] = 0;
   out_534012189001621552[252] = 0;
   out_534012189001621552[253] = 0;
   out_534012189001621552[254] = 0;
   out_534012189001621552[255] = 0;
   out_534012189001621552[256] = 0;
   out_534012189001621552[257] = 0;
   out_534012189001621552[258] = 0;
   out_534012189001621552[259] = 0;
   out_534012189001621552[260] = 0;
   out_534012189001621552[261] = 0;
   out_534012189001621552[262] = 0;
   out_534012189001621552[263] = 0;
   out_534012189001621552[264] = 0;
   out_534012189001621552[265] = 0;
   out_534012189001621552[266] = 1;
   out_534012189001621552[267] = 0;
   out_534012189001621552[268] = 0;
   out_534012189001621552[269] = 0;
   out_534012189001621552[270] = 0;
   out_534012189001621552[271] = 0;
   out_534012189001621552[272] = 0;
   out_534012189001621552[273] = 0;
   out_534012189001621552[274] = 0;
   out_534012189001621552[275] = 0;
   out_534012189001621552[276] = 0;
   out_534012189001621552[277] = 0;
   out_534012189001621552[278] = 0;
   out_534012189001621552[279] = 0;
   out_534012189001621552[280] = 0;
   out_534012189001621552[281] = 0;
   out_534012189001621552[282] = 0;
   out_534012189001621552[283] = 0;
   out_534012189001621552[284] = 0;
   out_534012189001621552[285] = 1;
   out_534012189001621552[286] = 0;
   out_534012189001621552[287] = 0;
   out_534012189001621552[288] = 0;
   out_534012189001621552[289] = 0;
   out_534012189001621552[290] = 0;
   out_534012189001621552[291] = 0;
   out_534012189001621552[292] = 0;
   out_534012189001621552[293] = 0;
   out_534012189001621552[294] = 0;
   out_534012189001621552[295] = 0;
   out_534012189001621552[296] = 0;
   out_534012189001621552[297] = 0;
   out_534012189001621552[298] = 0;
   out_534012189001621552[299] = 0;
   out_534012189001621552[300] = 0;
   out_534012189001621552[301] = 0;
   out_534012189001621552[302] = 0;
   out_534012189001621552[303] = 0;
   out_534012189001621552[304] = 1;
   out_534012189001621552[305] = 0;
   out_534012189001621552[306] = 0;
   out_534012189001621552[307] = 0;
   out_534012189001621552[308] = 0;
   out_534012189001621552[309] = 0;
   out_534012189001621552[310] = 0;
   out_534012189001621552[311] = 0;
   out_534012189001621552[312] = 0;
   out_534012189001621552[313] = 0;
   out_534012189001621552[314] = 0;
   out_534012189001621552[315] = 0;
   out_534012189001621552[316] = 0;
   out_534012189001621552[317] = 0;
   out_534012189001621552[318] = 0;
   out_534012189001621552[319] = 0;
   out_534012189001621552[320] = 0;
   out_534012189001621552[321] = 0;
   out_534012189001621552[322] = 0;
   out_534012189001621552[323] = 1;
}
void h_4(double *state, double *unused, double *out_6191552396506608484) {
   out_6191552396506608484[0] = state[6] + state[9];
   out_6191552396506608484[1] = state[7] + state[10];
   out_6191552396506608484[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_4468786875016897913) {
   out_4468786875016897913[0] = 0;
   out_4468786875016897913[1] = 0;
   out_4468786875016897913[2] = 0;
   out_4468786875016897913[3] = 0;
   out_4468786875016897913[4] = 0;
   out_4468786875016897913[5] = 0;
   out_4468786875016897913[6] = 1;
   out_4468786875016897913[7] = 0;
   out_4468786875016897913[8] = 0;
   out_4468786875016897913[9] = 1;
   out_4468786875016897913[10] = 0;
   out_4468786875016897913[11] = 0;
   out_4468786875016897913[12] = 0;
   out_4468786875016897913[13] = 0;
   out_4468786875016897913[14] = 0;
   out_4468786875016897913[15] = 0;
   out_4468786875016897913[16] = 0;
   out_4468786875016897913[17] = 0;
   out_4468786875016897913[18] = 0;
   out_4468786875016897913[19] = 0;
   out_4468786875016897913[20] = 0;
   out_4468786875016897913[21] = 0;
   out_4468786875016897913[22] = 0;
   out_4468786875016897913[23] = 0;
   out_4468786875016897913[24] = 0;
   out_4468786875016897913[25] = 1;
   out_4468786875016897913[26] = 0;
   out_4468786875016897913[27] = 0;
   out_4468786875016897913[28] = 1;
   out_4468786875016897913[29] = 0;
   out_4468786875016897913[30] = 0;
   out_4468786875016897913[31] = 0;
   out_4468786875016897913[32] = 0;
   out_4468786875016897913[33] = 0;
   out_4468786875016897913[34] = 0;
   out_4468786875016897913[35] = 0;
   out_4468786875016897913[36] = 0;
   out_4468786875016897913[37] = 0;
   out_4468786875016897913[38] = 0;
   out_4468786875016897913[39] = 0;
   out_4468786875016897913[40] = 0;
   out_4468786875016897913[41] = 0;
   out_4468786875016897913[42] = 0;
   out_4468786875016897913[43] = 0;
   out_4468786875016897913[44] = 1;
   out_4468786875016897913[45] = 0;
   out_4468786875016897913[46] = 0;
   out_4468786875016897913[47] = 1;
   out_4468786875016897913[48] = 0;
   out_4468786875016897913[49] = 0;
   out_4468786875016897913[50] = 0;
   out_4468786875016897913[51] = 0;
   out_4468786875016897913[52] = 0;
   out_4468786875016897913[53] = 0;
}
void h_10(double *state, double *unused, double *out_8347226084972414573) {
   out_8347226084972414573[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_8347226084972414573[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_8347226084972414573[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_4111169058602364742) {
   out_4111169058602364742[0] = 0;
   out_4111169058602364742[1] = 9.8100000000000005*cos(state[1]);
   out_4111169058602364742[2] = 0;
   out_4111169058602364742[3] = 0;
   out_4111169058602364742[4] = -state[8];
   out_4111169058602364742[5] = state[7];
   out_4111169058602364742[6] = 0;
   out_4111169058602364742[7] = state[5];
   out_4111169058602364742[8] = -state[4];
   out_4111169058602364742[9] = 0;
   out_4111169058602364742[10] = 0;
   out_4111169058602364742[11] = 0;
   out_4111169058602364742[12] = 1;
   out_4111169058602364742[13] = 0;
   out_4111169058602364742[14] = 0;
   out_4111169058602364742[15] = 1;
   out_4111169058602364742[16] = 0;
   out_4111169058602364742[17] = 0;
   out_4111169058602364742[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_4111169058602364742[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_4111169058602364742[20] = 0;
   out_4111169058602364742[21] = state[8];
   out_4111169058602364742[22] = 0;
   out_4111169058602364742[23] = -state[6];
   out_4111169058602364742[24] = -state[5];
   out_4111169058602364742[25] = 0;
   out_4111169058602364742[26] = state[3];
   out_4111169058602364742[27] = 0;
   out_4111169058602364742[28] = 0;
   out_4111169058602364742[29] = 0;
   out_4111169058602364742[30] = 0;
   out_4111169058602364742[31] = 1;
   out_4111169058602364742[32] = 0;
   out_4111169058602364742[33] = 0;
   out_4111169058602364742[34] = 1;
   out_4111169058602364742[35] = 0;
   out_4111169058602364742[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_4111169058602364742[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_4111169058602364742[38] = 0;
   out_4111169058602364742[39] = -state[7];
   out_4111169058602364742[40] = state[6];
   out_4111169058602364742[41] = 0;
   out_4111169058602364742[42] = state[4];
   out_4111169058602364742[43] = -state[3];
   out_4111169058602364742[44] = 0;
   out_4111169058602364742[45] = 0;
   out_4111169058602364742[46] = 0;
   out_4111169058602364742[47] = 0;
   out_4111169058602364742[48] = 0;
   out_4111169058602364742[49] = 0;
   out_4111169058602364742[50] = 1;
   out_4111169058602364742[51] = 0;
   out_4111169058602364742[52] = 0;
   out_4111169058602364742[53] = 1;
}
void h_13(double *state, double *unused, double *out_2707578914264125574) {
   out_2707578914264125574[0] = state[3];
   out_2707578914264125574[1] = state[4];
   out_2707578914264125574[2] = state[5];
}
void H_13(double *state, double *unused, double *out_7681060700349230714) {
   out_7681060700349230714[0] = 0;
   out_7681060700349230714[1] = 0;
   out_7681060700349230714[2] = 0;
   out_7681060700349230714[3] = 1;
   out_7681060700349230714[4] = 0;
   out_7681060700349230714[5] = 0;
   out_7681060700349230714[6] = 0;
   out_7681060700349230714[7] = 0;
   out_7681060700349230714[8] = 0;
   out_7681060700349230714[9] = 0;
   out_7681060700349230714[10] = 0;
   out_7681060700349230714[11] = 0;
   out_7681060700349230714[12] = 0;
   out_7681060700349230714[13] = 0;
   out_7681060700349230714[14] = 0;
   out_7681060700349230714[15] = 0;
   out_7681060700349230714[16] = 0;
   out_7681060700349230714[17] = 0;
   out_7681060700349230714[18] = 0;
   out_7681060700349230714[19] = 0;
   out_7681060700349230714[20] = 0;
   out_7681060700349230714[21] = 0;
   out_7681060700349230714[22] = 1;
   out_7681060700349230714[23] = 0;
   out_7681060700349230714[24] = 0;
   out_7681060700349230714[25] = 0;
   out_7681060700349230714[26] = 0;
   out_7681060700349230714[27] = 0;
   out_7681060700349230714[28] = 0;
   out_7681060700349230714[29] = 0;
   out_7681060700349230714[30] = 0;
   out_7681060700349230714[31] = 0;
   out_7681060700349230714[32] = 0;
   out_7681060700349230714[33] = 0;
   out_7681060700349230714[34] = 0;
   out_7681060700349230714[35] = 0;
   out_7681060700349230714[36] = 0;
   out_7681060700349230714[37] = 0;
   out_7681060700349230714[38] = 0;
   out_7681060700349230714[39] = 0;
   out_7681060700349230714[40] = 0;
   out_7681060700349230714[41] = 1;
   out_7681060700349230714[42] = 0;
   out_7681060700349230714[43] = 0;
   out_7681060700349230714[44] = 0;
   out_7681060700349230714[45] = 0;
   out_7681060700349230714[46] = 0;
   out_7681060700349230714[47] = 0;
   out_7681060700349230714[48] = 0;
   out_7681060700349230714[49] = 0;
   out_7681060700349230714[50] = 0;
   out_7681060700349230714[51] = 0;
   out_7681060700349230714[52] = 0;
   out_7681060700349230714[53] = 0;
}
void h_14(double *state, double *unused, double *out_4697260034120461682) {
   out_4697260034120461682[0] = state[6];
   out_4697260034120461682[1] = state[7];
   out_4697260034120461682[2] = state[8];
}
void H_14(double *state, double *unused, double *out_8432027731356382442) {
   out_8432027731356382442[0] = 0;
   out_8432027731356382442[1] = 0;
   out_8432027731356382442[2] = 0;
   out_8432027731356382442[3] = 0;
   out_8432027731356382442[4] = 0;
   out_8432027731356382442[5] = 0;
   out_8432027731356382442[6] = 1;
   out_8432027731356382442[7] = 0;
   out_8432027731356382442[8] = 0;
   out_8432027731356382442[9] = 0;
   out_8432027731356382442[10] = 0;
   out_8432027731356382442[11] = 0;
   out_8432027731356382442[12] = 0;
   out_8432027731356382442[13] = 0;
   out_8432027731356382442[14] = 0;
   out_8432027731356382442[15] = 0;
   out_8432027731356382442[16] = 0;
   out_8432027731356382442[17] = 0;
   out_8432027731356382442[18] = 0;
   out_8432027731356382442[19] = 0;
   out_8432027731356382442[20] = 0;
   out_8432027731356382442[21] = 0;
   out_8432027731356382442[22] = 0;
   out_8432027731356382442[23] = 0;
   out_8432027731356382442[24] = 0;
   out_8432027731356382442[25] = 1;
   out_8432027731356382442[26] = 0;
   out_8432027731356382442[27] = 0;
   out_8432027731356382442[28] = 0;
   out_8432027731356382442[29] = 0;
   out_8432027731356382442[30] = 0;
   out_8432027731356382442[31] = 0;
   out_8432027731356382442[32] = 0;
   out_8432027731356382442[33] = 0;
   out_8432027731356382442[34] = 0;
   out_8432027731356382442[35] = 0;
   out_8432027731356382442[36] = 0;
   out_8432027731356382442[37] = 0;
   out_8432027731356382442[38] = 0;
   out_8432027731356382442[39] = 0;
   out_8432027731356382442[40] = 0;
   out_8432027731356382442[41] = 0;
   out_8432027731356382442[42] = 0;
   out_8432027731356382442[43] = 0;
   out_8432027731356382442[44] = 1;
   out_8432027731356382442[45] = 0;
   out_8432027731356382442[46] = 0;
   out_8432027731356382442[47] = 0;
   out_8432027731356382442[48] = 0;
   out_8432027731356382442[49] = 0;
   out_8432027731356382442[50] = 0;
   out_8432027731356382442[51] = 0;
   out_8432027731356382442[52] = 0;
   out_8432027731356382442[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_2134324718959906218) {
  err_fun(nom_x, delta_x, out_2134324718959906218);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_7874553077816062449) {
  inv_err_fun(nom_x, true_x, out_7874553077816062449);
}
void pose_H_mod_fun(double *state, double *out_8129875391940694982) {
  H_mod_fun(state, out_8129875391940694982);
}
void pose_f_fun(double *state, double dt, double *out_5920058179701921818) {
  f_fun(state,  dt, out_5920058179701921818);
}
void pose_F_fun(double *state, double dt, double *out_534012189001621552) {
  F_fun(state,  dt, out_534012189001621552);
}
void pose_h_4(double *state, double *unused, double *out_6191552396506608484) {
  h_4(state, unused, out_6191552396506608484);
}
void pose_H_4(double *state, double *unused, double *out_4468786875016897913) {
  H_4(state, unused, out_4468786875016897913);
}
void pose_h_10(double *state, double *unused, double *out_8347226084972414573) {
  h_10(state, unused, out_8347226084972414573);
}
void pose_H_10(double *state, double *unused, double *out_4111169058602364742) {
  H_10(state, unused, out_4111169058602364742);
}
void pose_h_13(double *state, double *unused, double *out_2707578914264125574) {
  h_13(state, unused, out_2707578914264125574);
}
void pose_H_13(double *state, double *unused, double *out_7681060700349230714) {
  H_13(state, unused, out_7681060700349230714);
}
void pose_h_14(double *state, double *unused, double *out_4697260034120461682) {
  h_14(state, unused, out_4697260034120461682);
}
void pose_H_14(double *state, double *unused, double *out_8432027731356382442) {
  H_14(state, unused, out_8432027731356382442);
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
