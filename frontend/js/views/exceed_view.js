export const exceedViewTemplate = `
  <div class="panel">
    <h3>超标旁路挂起</h3>
    <table>
      <thead><tr><th>键</th><th>采样</th><th>指标</th><th>测值</th><th>限值</th><th>状态</th><th>旁路开放标志</th><th>封批互斥</th><th></th></tr></thead>
      <tbody>
        <tr v-for="h in exceeds" :key="h.id">
          <td>{{ h.hold_key }}</td><td>{{ h.sample_code }}</td><td>{{ h.exceed_metric }}</td>
          <td>{{ h.measured }}</td><td>{{ h.limit_value }}</td>
          <td :class="h.status==='开放'?'fail':'pass'">{{ h.status }}</td>
          <td :class="h.bypass_open ? 'fail' : 'pass'">{{ h.bypass_open ? "开放=true" : "开放=false" }}</td>
          <td :class="h.bypass_open ? 'fail' : 'muted'">{{ h.bypass_open ? "禁封批（升代键拒写）" : "可封批" }}</td>
          <td><button v-if="h.status==='开放'" class="secondary" style="width:auto;margin:0" @click="closeExceed(h.id)">闭环</button></td>
        </tr>
      </tbody>
    </table>
    <p class="muted" style="font-size:0.8rem">旁路开放标志为真时，封批页升代键拒写、对账页已封灯互斥；闭环后标志转 false 才准升代。</p>
  </div>`;
