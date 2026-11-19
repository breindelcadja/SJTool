/*
  Onboarding settings when it runs on the team's server (solutions_backend),
  next to Nicole's home page. Open it at http://127.0.0.1:8000/ : new visitors
  are sent here first, and after signing up they go to the home page.

  The code in this folder is a copy of the website/ folder (only this file
  differs), so the Netlify version and this one stay the same.
*/
window.SJTOOL_CONFIG = {
  homeUrl: "/",          // Nicole's home page
  supabaseUrl: "",       // leave empty here; profiles are saved in the browser
  supabaseAnonKey: "",
};
