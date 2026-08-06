import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, ExternalLink } from "lucide-react";

import { api } from "../lib/api";
import type { VersionInfo } from "../lib/types";
import { APP_VERSION, highest, isOutdated } from "../lib/version";

const RELEASES = "https://github.com/enrico1203/tgbackup/releases";

// The backend caches the Docker Hub answer for six hours, so asking more often than
// this would only cost a round trip to a value that cannot have changed.
const REFRESH_MS = 30 * 60 * 1000;

/** The banner shown at the top of the dashboard when the images on Docker Hub are
 *  newer than what is running here.
 *
 *  Backend and interface are two images published separately and are reported
 *  separately: pulling one and forgetting the other is exactly the state worth
 *  naming, and "you are on an old version" without saying which half would leave the
 *  user to guess. Nothing is rendered when both are current, when either version is
 *  unknown, or when the check could not reach Docker Hub. */
export default function UpdateBanner() {
  const { data } = useQuery({
    queryKey: ["version"],
    queryFn: () => api.get<VersionInfo>("/api/version"),
    refetchInterval: REFRESH_MS,
    staleTime: REFRESH_MS,
  });

  if (!data) return null;

  const backendOld = isOutdated(data.backend, data.latest_backend);
  const frontendOld = isOutdated(APP_VERSION, data.latest_frontend);
  if (!backendOld && !frontendOld) return null;

  const latest = highest(data.latest_backend, data.latest_frontend);
  const behind = [
    backendOld ? `backend ${data.backend}` : null,
    frontendOld ? `interface ${APP_VERSION}` : null,
  ].filter(Boolean);

  return (
    <div className="update-banner">
      <ArrowUpCircle size={22} className="update-banner-icon" />

      <div className="update-banner-body">
        <div className="update-banner-title">tgbackup {latest} is available</div>
        <div className="update-banner-text">
          This installation runs {behind.join(" and ")}. Pull the new images and start
          them again: the container migrates its own database, jobs resume by
          themselves and nothing in the index is touched.
        </div>
        <div className="update-banner-command mono">docker compose pull &amp;&amp; docker compose up -d</div>
      </div>

      <a className="btn small" href={RELEASES} target="_blank" rel="noreferrer">
        Release notes
        <ExternalLink size={13} />
      </a>
    </div>
  );
}
