const app = require('./app.json').expo;

module.exports = () => ({
  ...app,
  extra: {
    ...app.extra,
    apiUrl: process.env.EXPO_PUBLIC_API_URL?.trim() || undefined,
  },
});
