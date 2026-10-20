#pragma once
// Kept in a header so the Arduino prototype generator sees it before use.
struct Sample {
  float v_tds, v_ec, v_ph, v_turb;
  float tds_ppm, ec_uscm, ph, turb_ntu;
  float v_ldr_l, v_ldr_r;
};
