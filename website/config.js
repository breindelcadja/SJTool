/*
  SJTool website settings.

  DEMO MODE (what you get with the two values below left empty):
  the site works with sample pitches, and sign-ups are only saved in your own
  browser. Good for trying it out and showing the design.

  TO GO LIVE with real sign-ups and personalized feeds:
  1. Set up Supabase (steps at the top of "solutions or not/supabase_sync.py").
  2. In your Supabase project, open Project Settings > API (or the Connect
     button) and copy the "Project URL" and the "anon public" key.
  3. Paste them below, commit and push.

  The anon key is meant to be public: the privacy rules in supabase_schema.sql
  stop it from reading anyone's email. NEVER put the service_role key or the
  database password here. Those are secret.
*/
window.SJTOOL_CONFIG = {
  supabaseUrl: "",       // e.g. "https://abcdefghijklm.supabase.co"
  supabaseAnonKey: "",   // the long "anon public" key
};
