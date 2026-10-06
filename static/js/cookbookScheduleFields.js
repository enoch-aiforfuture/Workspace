// Pure schedule payload for the Cookbook serve form.
// scheduled_time and cron hour/minute are the picked local wall clock when
// the browser has an IANA timezone. compute_next_run interprets both in that
// zone. With no zone, the clock is converted to UTC and timezone is omitted
// so existing UTC tasks keep their meaning.
(function (root) {
  const DAYS = [
    { k: "MO", idx: 0 },
    { k: "TU", idx: 1 },
    { k: "WE", idx: 2 },
    { k: "TH", idx: 3 },
    { k: "FR", idx: 4 },
    { k: "SA", idx: 5 },
    { k: "SU", idx: 6 },
  ];

  function buildCookbookScheduleFields(startTime, days, timeZone) {
    const [sh, sm] = String(startTime || "").split(":").map(Number);
    let hour = sh;
    let minute = sm;
    const sched = {};
    if (timeZone) {
      sched.timezone = timeZone;
    } else {
      const d = new Date();
      d.setHours(sh, sm, 0, 0);
      hour = d.getUTCHours();
      minute = d.getUTCMinutes();
    }
    const hhmm = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
    const picked = Array.isArray(days) ? days : [];
    const allDays = picked.length === 7;
    const weekdaysOnly = picked.length === 5 && ["MO", "TU", "WE", "TH", "FR"].every(d => picked.includes(d));
    if (allDays) {
      sched.schedule = "daily";
      sched.scheduled_time = hhmm;
    } else if (weekdaysOnly) {
      sched.schedule = "cron";
      sched.cron_expression = `${minute} ${hour} * * 1-5`;
    } else if (picked.length === 1) {
      const dayIdx = DAYS.find(d => d.k === picked[0]).idx;
      sched.schedule = "weekly";
      sched.scheduled_time = hhmm;
      sched.scheduled_day = dayIdx;
    } else {
      const dayNum = picked.map(k => {
        const i = DAYS.find(d => d.k === k).idx;
        return i === 6 ? 0 : i + 1;
      });
      sched.schedule = "cron";
      sched.cron_expression = `${minute} ${hour} * * ${dayNum.join(",")}`;
    }
    return sched;
  }

  root.buildCookbookScheduleFields = buildCookbookScheduleFields;
})(typeof globalThis !== "undefined" ? globalThis : this);
