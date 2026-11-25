export const sealViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>封批快照</h3>
      <table>
        <thead><tr><th>封批号</th><th>采样</th><th>代</th><th>快照</th><th>封批态</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="s in seals" :key="s.id">
            <td>{{ s.seal_ref }}</td><td>{{ s.sample_code }}</td><td>{{ s.sample_generation }}</td>
            <td>{{ s.snapshot }}</td>
            <td :class="s.status==='已封批' ? 'pass' : (s.status==='草稿作废' ? 'fail' : 'warn')">{{ s.status }}</td>
            <td>{{ s.sealed_at }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="font-size:0.8rem">仅“已封批”为正式区快照；“影子草稿”不升代、不计已封；“草稿作废”为旁路未闭拒升正留痕。</p>
    </div>
    <div class="panel">
      <h3>执行封批</h3>
      <label>可封批采样（旁路开放批已剔除）</label>
      <select v-model.number="sealForm.sample_id">
        <option v-for="s in sealableSamples" :key="s.id" :value="s.id">
          {{ s.sample_code }} · {{ s.status }}<template v-if="s.bypass_open"> · 旁路开放禁封</template>
        </option>
      </select>
      <label>快照（可空自动）</label><input v-model="sealForm.snapshot"/>
      <div class="actions">
        <button class="warn" :disabled="sealSelectedBypassOpen" @click="createSeal">封批升代</button>
        <button class="secondary" :disabled="sealSelectedBypassOpen" @click="createSealDraft">只落影子草稿</button>
        <button class="secondary" @click="promoteSealDraft">草稿原子升正</button>
      </div>
      <p v-if="sealSelectedBypassOpen" class="fail" style="font-size:0.85rem">
        旁路挂起仍开放：升代键已禁写，封批意图只能进影子草稿且须闭环后升正。
      </p>
    </div>
  </div>`;
