import { BRAND } from "../brand";

/**
 * Kurum işareti: favicon ile aynı dosya (`BRAND.logoUrl`, public/brand/logo.svg). Yanında her zaman kurum
 * adı yazılı olduğundan süs görseli sayılır (alt="").
 */
export function Logo({ size = 32 }: { size?: number }) {
  return <img className="logo" src={BRAND.logoUrl} width={size} height={size} alt="" draggable={false} />;
}
