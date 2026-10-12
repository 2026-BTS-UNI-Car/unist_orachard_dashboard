"""Default-OFF display wrapper; native admission/clock/math remain in oracle."""
from native_map_owner import NativeAcceptedMapOwner, _MapCapacityReached, require
from batched_rays import raster_rays, raster_ray_cells, RayCapacityReached, RayRasterCancelled
import numpy as np

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
    def _ray_cells(self,frame,pose):
        if not self.__batched_rays_enabled:return super()._ray_cells(frame,pose)
        try:
            return raster_ray_cells(frame,pose,resolution_m=self._resolution,
                max_cells=self._capacity,max_ray_visits=self._visits,cancelled=self._stop.is_set)
        except RayCapacityReached:raise _MapCapacityReached() from None
        except RayRasterCancelled:
            return np.zeros((0,2),dtype=np.int64),np.zeros((0,2),dtype=np.int64)
