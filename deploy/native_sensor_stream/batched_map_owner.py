"""Default-OFF display wrapper; native admission/clock/math remain in oracle."""
from native_map_owner import NativeAcceptedMapOwner, _MapCapacityReached, require
from batched_rays import raster_rays, RayCapacityReached, RayRasterCancelled

class BatchedNativeAcceptedMapOwner(NativeAcceptedMapOwner):
    def __init__(self,*,batched_rays_enabled=False,**kwargs):
        require(type(batched_rays_enabled) is bool,'typed_batched_ray_feature_required')
        self.__batched_rays_enabled=batched_rays_enabled
        super().__init__(**kwargs)
    @property
    def batched_rays_enabled(self):return self.__batched_rays_enabled
    def _rays(self,frame,pose):
        if not self.__batched_rays_enabled:return super()._rays(frame,pose)
        try:
            return raster_rays(frame,pose,resolution_m=self._resolution,
                max_cells=self._capacity,max_ray_visits=self._visits,cancelled=self._stop.is_set)
        except RayCapacityReached:raise _MapCapacityReached() from None
        except RayRasterCancelled:
            # No partial raster escapes. Original prepare/commit publication
            # fences still observe the already-latched Stop and return None.
            return set(),set()
