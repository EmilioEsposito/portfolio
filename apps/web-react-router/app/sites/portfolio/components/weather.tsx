import { cn } from "~/lib/utils";
import { format, isWithinInterval } from "date-fns";
import { useEffect, useState } from "react";

interface WeatherAtLocation {
  latitude: number;
  longitude: number;
  generationtime_ms: number;
  utc_offset_seconds: number;
  timezone: string;
  timezone_abbreviation: string;
  elevation: number;
  current_units: {
    time: string;
    interval: string;
    temperature_2m: string;
  };
  current: {
    time: string;
    interval: number;
    temperature_2m: number;
  };
  hourly_units: {
    time: string;
    temperature_2m: string;
  };
  hourly: {
    time: string[];
    temperature_2m: number[];
  };
  daily_units: {
    time: string;
    sunrise: string;
    sunset: string;
  };
  daily: {
    time: string[];
    sunrise: string[];
    sunset: string[];
  };
}

const SAMPLE: WeatherAtLocation = {
  latitude: 37.763283,
  longitude: -122.41286,
  generationtime_ms: 0.027894973754882812,
  utc_offset_seconds: 0,
  timezone: "GMT",
  timezone_abbreviation: "GMT",
  elevation: 18,
  current_units: { time: "iso8601", interval: "seconds", temperature_2m: "°C" },
  current: { time: "2024-10-07T19:30", interval: 900, temperature_2m: 29.3 },
  hourly_units: { time: "iso8601", temperature_2m: "°C" },
  hourly: {
    time: [
      "2024-10-07T00:00",
      "2024-10-07T01:00",
      "2024-10-07T02:00",
      "2024-10-07T03:00",
      "2024-10-07T04:00",
      "2024-10-07T05:00",
      "2024-10-07T06:00",
      "2024-10-07T07:00",
      "2024-10-07T08:00",
      "2024-10-07T09:00",
      "2024-10-07T10:00",
      "2024-10-07T11:00",
      "2024-10-07T12:00",
      "2024-10-07T13:00",
      "2024-10-07T14:00",
      "2024-10-07T15:00",
      "2024-10-07T16:00",
      "2024-10-07T17:00",
      "2024-10-07T18:00",
      "2024-10-07T19:00",
      "2024-10-07T20:00",
      "2024-10-07T21:00",
      "2024-10-07T22:00",
      "2024-10-07T23:00",
    ],
    temperature_2m: [
      36.6, 32.8, 29.5, 28.6, 29.2, 28.2, 27.5, 26.6, 26.5, 26, 25, 23.5, 23.9,
      24.2, 22.9, 21, 24, 28.1, 31.4, 33.9, 32.1, 28.9, 26.9, 25.2,
    ],
  },
  daily_units: {
    time: "iso8601",
    sunrise: "iso8601",
    sunset: "iso8601",
  },
  daily: {
    time: ["2024-10-07"],
    sunrise: ["2024-10-07T07:15"],
    sunset: ["2024-10-07T19:00"],
  },
};

function n(num: number): number {
  return Math.ceil(num);
}

export function Weather({
  weatherAtLocation = SAMPLE,
}: {
  weatherAtLocation?: WeatherAtLocation;
}) {
  const currentHigh = Math.max(
    ...weatherAtLocation.hourly.temperature_2m.slice(0, 24)
  );
  const currentLow = Math.min(
    ...weatherAtLocation.hourly.temperature_2m.slice(0, 24)
  );

  const isDay = isWithinInterval(new Date(weatherAtLocation.current.time), {
    start: new Date(weatherAtLocation.daily.sunrise[0]),
    end: new Date(weatherAtLocation.daily.sunset[0]),
  });

  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };

    handleResize();
    window.addEventListener("resize", handleResize);

    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const hoursToShow = isMobile ? 5 : 6;

  // Find the index of the current time or the next closest time
  const currentTimeIndex = weatherAtLocation.hourly.time.findIndex(
    (time) => new Date(time) >= new Date(weatherAtLocation.current.time)
  );

  // Slice the arrays to get the desired number of items
  const displayTimes = weatherAtLocation.hourly.time.slice(
    currentTimeIndex,
    currentTimeIndex + hoursToShow
  );
  const displayTemperatures = weatherAtLocation.hourly.temperature_2m.slice(
    currentTimeIndex,
    currentTimeIndex + hoursToShow
  );

  return (
    <div
      className={cn(
        "flex flex-col gap-4 rounded-2xl p-4 skeleton-bg max-w-[500px]",
        {
          "bg-blue-400": isDay,
        },
        {
          "bg-indigo-900": !isDay,
        }
      )}
    >
      <div className="flex flex-row justify-between items-center">
        <div className="flex flex-row gap-2 items-center">
          <div
            className={cn(
              "size-10 rounded-full skeleton-div",
              {
                "bg-yellow-300": isDay,
              },
              {
                "bg-indigo-100": !isDay,
              }
            )}
          />
          <div className="text-4xl font-medium text-blue-50">
            {n(weatherAtLocation.current.temperature_2m)}
            {weatherAtLocation.current_units.temperature_2m}
          </div>
        </div>

        <div className="text-blue-50">{`H:${n(currentHigh)}° L:${n(currentLow)}°`}</div>
      </div>

      <div className="flex flex-row justify-between">
        {displayTimes.map((time, index) => (
          <div key={time} className="flex flex-col items-center gap-1">
            <div className="text-blue-100 text-xs">
              {format(new Date(time), "ha")}
            </div>
            <div
              className={cn(
                "size-6 rounded-full skeleton-div",
                {
                  "bg-yellow-300": isDay,
                },
                {
                  "bg-indigo-200": !isDay,
                }
              )}
            />
            <div className="text-blue-50 text-sm">
              {n(displayTemperatures[index])}
              {weatherAtLocation.hourly_units.temperature_2m}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
