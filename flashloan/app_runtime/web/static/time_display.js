(function () {
  "use strict";

  const LOCAL_TIMEZONE = "Asia/Shanghai";

  function parseDate(value) {
    if (!value) return null;
    if (value instanceof Date) return value;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  window.formatLocalDisplayTime = function (value, options) {
    const parsed = parseDate(value);
    if (!parsed) return "-";
    try {
      return parsed.toLocaleString("zh-CN", {
        timeZone: LOCAL_TIMEZONE,
        hour12: false,
        ...(options || {}),
      });
    } catch (error) {
      return parsed.toLocaleString("zh-CN", { hour12: false });
    }
  };

  window.formatLocalDisplayNow = function () {
    return window.formatLocalDisplayTime(new Date().toISOString());
  };
})();
