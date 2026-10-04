// Immutable ahead-of-time maps are applied only up to the media playback clock.
export class ArchiveMapBuffer {
  constructor(window){Object.assign(this,window);}
  contains(time){return time>=this.start_s && time<=this.end_s;}
  at(time,version=-1,epoch=null){
    if(!this.contains(time))return null;
    const frames=this.events.filter(e=>e.frame_t_s<=time);
    const latest=frames.at(-1)||this.seed;
    const incremental=epoch===this.seed.tile_epoch && version>=this.seed.tile_version && version<=latest.tile_version;
    let tiles=new Map(),path=[],reset=!incremental,packet=this.seed;
    if(!incremental){for(const t of this.seed.tiles||[])tiles.set(`${t.x},${t.y}`,t);path=[...(this.seed.path||[])];}
    for(const row of frames){
      if(incremental && row.tile_version<=version)continue;
      if(row.tiles_reset){tiles.clear();path=[];reset=true;}
      for(const tile of row.tiles||[])tiles.set(`${tile.x},${tile.y}`,tile);
      path.push(...(row.path||[]));packet=row;
    }
    return {...latest,tiles:[...tiles.values()],path,tiles_reset:reset,path_append:!reset};
  }
}
