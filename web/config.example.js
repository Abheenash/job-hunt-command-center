// Copy to config.js and fill from `terraform output`. The deploy script
// (scripts/deploy_web.sh) generates config.js automatically.
window.JHCC_CONFIG = {
  region: "us-east-1",
  userPoolId: "us-east-1_XXXXXXXXX",
  clientId: "XXXXXXXXXXXXXXXXXXXXXXXXXX",
  apiBase: "https://XXXXXXXXXX.execute-api.us-east-1.amazonaws.com",
};
