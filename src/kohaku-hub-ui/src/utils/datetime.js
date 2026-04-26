import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import timezone from "dayjs/plugin/timezone";
import utc from "dayjs/plugin/utc";

dayjs.extend(relativeTime);
dayjs.extend(utc);
dayjs.extend(timezone);

let browserTimeZone = "UTC";

export function initializeBrowserTimezone() {
  if (typeof Intl !== "undefined") {
    browserTimeZone =
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      dayjs.tz.guess() ||
      "UTC";
  }

  return browserTimeZone;
}

function parseBrowserTime(value) {
  const timeZone = initializeBrowserTimezone();

  if (typeof value === "number") {
    return dayjs.unix(value).tz(timeZone);
  }

  if (value instanceof Date) {
    return dayjs(value).tz(timeZone);
  }

  if (typeof value === "string") {
    if (value.endsWith("Z")) {
      const utcParsed = dayjs(value).tz(timeZone);
      const localParsed = dayjs.tz(value.slice(0, -1), timeZone);

      if (
        utcParsed.isValid() &&
        localParsed.isValid() &&
        utcParsed.isAfter(dayjs()) &&
        !localParsed.isAfter(dayjs())
      ) {
        return localParsed;
      }

      if (utcParsed.isValid()) {
        return utcParsed;
      }
    }

    return dayjs.tz(value, timeZone);
  }

  return dayjs(value).tz(timeZone);
}

export function formatRelativeTime(value, fallback = "never") {
  if (!value) return fallback;

  const parsed = parseBrowserTime(value);
  return parsed.isValid() ? parsed.fromNow() : fallback;
}

export function formatUnixRelativeTime(value, fallback = "Unknown") {
  if (!value) return fallback;

  const parsed = parseBrowserTime(value);
  return parsed.isValid() ? parsed.fromNow() : fallback;
}
