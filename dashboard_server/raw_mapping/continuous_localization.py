"""Causal fixed-frame scan/keyframe tracking. A lost fit never resets the map.

Gyro is projected on gravity using the native quaternion; no absolute IMU yaw,
wheel signal, known orchard, camera or invented sensor mounting angle is used.
Failures retain the last pose and cells, without integrating an unknown pose.
"""
import math
import numpy as np
from scipy.spatial.transform import Rotation
from .raw_localization import (source_stamp,quaternion,scan_points,surfaces,
    rotate,registration,fit_surfaces,angular_change)
from .raw_point_support import point_support_registration,proposal
from .fit_workers import evaluate


class ContinuousLocalization:
    def __init__(self):
        self.pose=np.zeros(3);self.previous=None;self.anchor=None;self.segment=0
        self.last_seen=None;self.gyro_time=None;self.gyro_rate=None
        self.gyro_integral=0.;self.gyro_valid_s=0.;self.velocity=None
        self.rejected=0;self.accepted=0
        self.last_recovery=-float('inf')
        self.position_prior=None
        self.gps_speed=None;self.direction=None;self.last_geometry_stamp=None
        self.motion_prior_count=0;self.path_break_pending=False

    def gps(self,sample):
        try:
            speed=float(sample['speed_mps']);stamp=sample['stamp']['sec']+sample['stamp']['nanosec']*1e-9
            if sample.get('valid') is True and math.isfinite(speed) and 0<=speed<=3 and 0<float(sample['h_acc_m'])<=10:
                self.gps_speed=(stamp,speed)
        except (KeyError,ValueError,TypeError):pass

    def _motion_bridge(self,stamp):
        if self.direction is None or self.gps_speed is None or self.last_geometry_stamp is None:return None
        dt=stamp-self.previous['stamp'];gps_t,speed=self.gps_speed
        if not 0<dt<=1.5 or stamp-self.last_geometry_stamp>1.5 or not 0<=stamp-gps_t<=1.5:return None
        if self.gyro_valid_s-self.previous['gyro_valid']<dt*.8:return None
        yaw=self.gyro_integral-self.previous['gyro']
        return dict(xy=rotate(yaw*.5)@self.direction*speed*dt,yaw=yaw,rmse_m=0.)

    def gps_prior(self,stamp,local_xy,scale_m):
        self.position_prior=(float(stamp),np.asarray(local_xy,dtype=float),max(1.,float(scale_m)))

    def _recover(self,current,q,stamp):
        reference=self.previous;dt=stamp-reference['stamp']
        if dt<.6 or stamp-self.last_recovery<.75:return None
        self.last_recovery=stamp
        gyro=self.gyro_integral-reference['gyro']
        rotation=Rotation.from_quat(reference['q']).inv()*Rotation.from_quat(q)
        gravity=Rotation.from_quat(reference['q']).inv().apply([0.,0.,1.])
        quaternion_yaw=float(np.dot(rotation.as_rotvec(),gravity))
        radius=min(10.,max(1.5,dt*1.5));step=.5 if radius<=4 else 1.
        center=np.zeros(2)
        if self.position_prior and self.position_prior[2]<=5 and abs(self.position_prior[0]-stamp)<1.5:
            center=rotate(-reference['pose'][2])@(self.position_prior[1]-reference['pose'][:2])
            radius=min(10.,max(2.,3*self.position_prior[2]));step=1.
        xy=center+np.array([(x,y) for x in np.arange(-radius,radius+.01,step) for y in np.arange(-radius,radius+.01,step)])
        points=current.points[::max(1,len(current.points)//120)];ranked=[]
        for yaw in (gyro,quaternion_yaw):
            moved=points@rotate(yaw).T
            distance,_=reference['surfaces'].tree.query((moved[None,:,:]+xy[:,None,:]).reshape(-1,2),workers=1)
            distance=distance.reshape((len(xy),len(points)))
            score=np.mean(np.minimum(distance,.6),axis=1)
            for index in np.argsort(score)[:4]:ranked.append((score[index],yaw,xy[index]))
        candidates=[]
        for _,yaw,seed in sorted(ranked,key=lambda a:a[0])[:6]:
            fitted=fit_surfaces(reference['surfaces'],current,yaw,seed,radius*1.5+np.linalg.norm(center),max(abs(yaw)+math.radians(8),math.radians(15)))
            if fitted and fitted['coverage']>=.55 and fitted['rmse_m']<=.05:
                candidates.append(fitted)
        if not candidates:return None
        candidates.sort(key=lambda a:(-a['coverage'],a['rmse_m']));best=candidates[0]
        for other in candidates[1:]:
            different=np.linalg.norm(other['xy']-best['xy'])>.15 or abs(other['yaw']-best['yaw'])>math.radians(2)
            if different and other['coverage']>=best['coverage']-.05 and other['rmse_m']<=max(.02,best['rmse_m']*1.5):return None
        return best

    def imu(self,message):
        try:
            t=source_stamp(message);q=quaternion(message)
            w=np.array([message['angular_velocity'][k] for k in 'xyz'],dtype=float)
            if (message.get('angular_velocity_covariance') or [0])[0]<0 or not np.all(np.isfinite(w)):return
            rate=float(Rotation.from_quat(q).apply(w)[2])
            if self.gyro_time is not None:
                dt=t-self.gyro_time
                if dt<=0:return
                if dt<=.20:
                    self.gyro_integral+=(rate+self.gyro_rate)*.5*dt;self.gyro_valid_s+=dt
            self.gyro_time=t;self.gyro_rate=rate
        except (ValueError,KeyError,TypeError):return

    def reject(self,stamp,reason):
        self.rejected+=1
        return dict(stamp=stamp,accepted=False,reason=reason,segment=0,pose=None,
                    tracking='relocalizing',last_pose=self.pose.tolist(),autonomy_eligible=False)

    def _fit(self,reference,current,q,stamp):
        dt=stamp-reference['stamp'];angle=angular_change(reference['q'],q)
        predicted=rotate(-reference['pose'][2])@(self.pose[:2]-reference['pose'][:2])
        delta_yaw=self.pose[2]-reference['pose'][2]
        gyro=self.gyro_integral-reference['gyro'];coverage=self.gyro_valid_s-reference['gyro_valid']
        prior=None
        if dt>0 and coverage>=dt*.8:
            # Noise/bias floor grows with keyframe age. A prior is not a zero
            # rotation rule and never overrides the scan observability gates.
            prior=(gyro,math.radians(.35+.3*min(dt,10)))
        max_xy=min(3.,max(.35,.12+3*min(dt,1.)))
        max_yaw=min(math.radians(70),angle+math.radians(5))
        seeds=[(gyro if prior else delta_yaw,predicted),(delta_yaw,predicted)]
        candidates=[]
        def solve(seed):
            theta,xy=seed
            return fit_surfaces(reference['surfaces'],current,theta,xy,max_xy,max_yaw,yaw_prior=prior)
        for result in evaluate(solve,seeds):
            if result and (prior is None or abs(result['yaw']-gyro)<=max(math.radians(2),3*prior[1])):
                candidates.append(result)
        if not candidates:return None
        candidates.sort(key=lambda r:(-r['coverage'],r['rmse_m']))
        return candidates[0]

    def add(self,scan,imu):
        stamp=source_stamp(scan)
        if self.last_seen is not None and stamp<=self.last_seen:return self.reject(stamp,'scan_clock_nonmonotonic')
        self.last_seen=stamp
        try:
            if imu is None or abs(source_stamp(imu)-stamp)>.10:raise ValueError('waiting_for_source_aligned_IMU')
            q=quaternion(imu);a=np.array([imu['linear_acceleration'][k] for k in 'xyz'])
            if not np.all(np.isfinite(a)) or abs(np.linalg.norm(a)-9.80665)>6:raise ValueError('IMU_shock_or_invalid_acceleration')
            current=surfaces(scan_points(scan))
            # Leaves and compact trunks are not all planar surfaces. Pose
            # observability is checked on measured normals in two directions;
            # absent normals must not count as failed normal correspondences.
            current.denominator=len(current.points)
        except (ValueError,KeyError,TypeError) as error:return self.reject(stamp,str(error))
        used='initial_fixed_frame';fit=None
        if self.previous:
            reference=self.previous;dt=stamp-reference['stamp']
            candidate=registration(reference['surfaces'],current,angular_change(reference['q'],q),dt,self.velocity)
            if candidate['accepted']:fit=candidate;used='adjacent_surface_fit'
            weighted=self._fit(reference,current,q,stamp)
            if weighted and (fit is None or (weighted['coverage']>=fit['coverage']-.03
                    and weighted['rmse_m']<=max(.015,fit['rmse_m']*1.15))):
                fit=weighted;used='adjacent_signed_IMU_fit'
            # A keyframe must explain the scan at least as well as the adjacent
            # observation. Long/noisy gyro integration cannot veto a good fit.
            if stamp-self.anchor['stamp']<5:
                anchored=self._fit(self.anchor,current,q,stamp)
                if anchored and (fit is None or (anchored['coverage']>=fit['coverage']-.03
                        and anchored['rmse_m']<=max(.02,fit['rmse_m']*1.3))):
                    fit=anchored;reference=self.anchor;used='fixed_keyframe_IMU_fit'
            if fit is None:
                reference=self.previous;fit=self._fit(reference,current,q,stamp);used='adjacent_IMU_fit'
            if fit is None:
                # Dense proposal remains independently surface-validated. It
                # cannot silently introduce a yaw unsupported by raw gyro.
                dt=stamp-self.previous['stamp'];reference=self.previous
                if 0<dt<=3:
                    alternative=point_support_registration(reference['surfaces'],current,angular_change(reference['q'],q),dt,self.velocity)
                    gyro=self.gyro_integral-reference['gyro'];covered=self.gyro_valid_s-reference['gyro_valid']
                    if alternative['accepted'] and (covered<dt*.8 or abs(alternative['yaw']-gyro)<math.radians(3)):
                        fit=alternative;used='dense_surface_validated_fit'
                if fit is None and 0<dt<=.35 and covered>=dt*.8:
                    prediction=np.zeros(2) if self.velocity is None else self.velocity*dt
                    partial=proposal(reference['surfaces'],current,gyro,prediction,
                        min(1.,.1+3*dt),abs(gyro)+math.radians(2),
                        min_coverage=.5,max_rmse=.13,min_normal_support=40,min_normal_eigen=.04,max_distance=.25)
                    if partial and abs(partial['yaw']-gyro)<math.radians(.75):
                        fit=partial;used='partial_overlap_with_gyro_validation'
            if fit is None:
                reference=self.previous;fit=self._recover(current,q,stamp);used='observed_scan_relocalization'
            if fit is None:
                reference=self.previous;fit=self._motion_bridge(stamp);used='short_GNSS_speed_IMU_motion_prior'
            if fit is None:return self.reject(stamp,'fixed_frame_registration_unresolved')
            new_pose=np.r_[reference['pose'][:2]+rotate(reference['pose'][2])@fit['xy'],reference['pose'][2]+fit['yaw']]
            if self.position_prior and abs(self.position_prior[0]-stamp)<1.5:
                if np.linalg.norm(new_pose[:2]-self.position_prior[1])>max(5.,4*self.position_prior[2]):
                    return self.reject(stamp,'GNSS_position_innovation_exceeded')
            dt=stamp-self.previous['stamp']
            self.velocity=rotate(-new_pose[2])@(new_pose[:2]-self.pose[:2])/max(dt,.001)
            self.pose=new_pose
            if used!='short_GNSS_speed_IMU_motion_prior' and np.linalg.norm(self.velocity)>.08:
                direction=self.velocity/np.linalg.norm(self.velocity)
                self.direction=direction if self.direction is None else .8*self.direction+.2*direction
                self.direction/=np.linalg.norm(self.direction)
        if used!='short_GNSS_speed_IMU_motion_prior':self.last_geometry_stamp=stamp
        else:self.motion_prior_count+=1;self.path_break_pending=True
        self.accepted+=1
        # Sampling is for the query scan only. Sparse reference anchors created
        # artificial empty gaps along distant walls and rejected valid matches.
        dense_reference=surfaces(current.dense,maximum=len(current.dense))
        value=dict(stamp=stamp,surfaces=dense_reference,q=q,pose=self.pose.copy(),gyro=self.gyro_integral,gyro_valid=self.gyro_valid_s)
        self.previous=value
        if (self.anchor is None or used=='short_GNSS_speed_IMU_motion_prior' or np.linalg.norm(self.pose[:2]-self.anchor['pose'][:2])>1.25
                or abs(self.pose[2]-self.anchor['pose'][2])>math.radians(15)):
            self.anchor=value
        path_break=self.path_break_pending or used=='observed_scan_relocalization'
        if used!='short_GNSS_speed_IMU_motion_prior':self.path_break_pending=False
        return dict(stamp=stamp,segment=0,pose=self.pose.copy(),accepted=True,reason=used,
            tracking='tracking',accepted_scans=self.accepted,rejected_scans=self.rejected,
            map_eligible=used!='short_GNSS_speed_IMU_motion_prior',
            motion_prior=used=='short_GNSS_speed_IMU_motion_prior',
            path_break=path_break,motion_prior_scans=self.motion_prior_count,
            fit_rmse_m=fit['rmse_m'] if fit else 0.,autonomy_eligible=False)
