# dense vs hybrid 逐题胜负分析（自动生成）

> 可答 132 题，top_k=6，collection=`eval_hard_kb`

| 结果 | 题数 |
|---|---|
| hybrid 排名更靠前 | 18 |
| dense 排名更靠前 | 4 |
| 排名相同 | 110 |
| 两者都未命中 | 0 |

## hybrid 赢的案例（dense 失败模式）

**h001**　星辰 X1 的处理器是多少？

- 期望：`['星辰X1.md']`
- dense 排名：2　top3：`['星辰X1Standard.md', '星辰X1.md', '星辰X1Ultra.md']`
- hybrid 排名：1　top3：`['星辰X1.md', '星辰X1Pro.md', '星辰X1Ultra.md']`

**h003**　星辰 X1 的质保年限是多少？

- 期望：`['星辰X1.md']`
- dense 排名：2　top3：`['星辰X1Standard.md', '星辰X1.md', '星辰X1Mini.md']`
- hybrid 排名：1　top3：`['星辰X1.md', '星辰X1Pro.md', '星辰X1Standard.md']`

**h005**　星辰 X2 的快充是多少？

- 期望：`['星辰X2.md']`
- dense 排名：2　top3：`['星辰X2Lite.md', '星辰X2.md', '星辰X2Standard.md']`
- hybrid 排名：1　top3：`['星辰X2.md', '星辰X2Mini.md', '星辰X2Plus.md']`

**h006**　星辰 X2 的快充是多少？

- 期望：`['星辰X2.md']`
- dense 排名：2　top3：`['星辰X2Lite.md', '星辰X2.md', '星辰X2Standard.md']`
- hybrid 排名：1　top3：`['星辰X2.md', '星辰X2Mini.md', '星辰X2Plus.md']`

**h010**　星云 Note 的处理器是多少？

- 期望：`['星云Note.md']`
- dense 排名：2　top3：`['星云NoteStandard.md', '星云Note.md', '星云NoteMini.md']`
- hybrid 排名：1　top3：`['星云Note.md', '星云NotePro.md', '星云NoteLite.md']`

**h014**　星云 Fold 的屏幕是多少？

- 期望：`['星云Fold.md']`
- dense 排名：2　top3：`['星云FoldStandard.md', '星云Fold.md', '星云FoldMini.md']`
- hybrid 排名：1　top3：`['星云Fold.md', '星云FoldMini.md', '星云FoldPro.md']`

**h015**　星云 Fold 的质保年限是多少？

- 期望：`['星云Fold.md']`
- dense 排名：2　top3：`['星云FoldStandard.md', '星云Fold.md', '星云FoldPlus.md']`
- hybrid 排名：1　top3：`['星云Fold.md', '星云FoldAir.md', '星云FoldPro.md']`

**h023**　星澜 Buds 的防水等级是多少？

- 期望：`['星澜Buds.md']`
- dense 排名：3　top3：`['星澜BudsStandard.md', '星澜BudsPlus.md', '星澜Buds.md']`
- hybrid 排名：1　top3：`['星澜Buds.md', '星澜BudsPlus.md', '星澜BudsPro.md']`

**h024**　星澜 Buds 的续航是多少？

- 期望：`['星澜Buds.md']`
- dense 排名：4　top3：`['星澜BudsStandard.md', '星澜BudsPlus.md', '星澜BudsPro.md']`
- hybrid 排名：1　top3：`['星澜Buds.md', '星澜BudsPro.md', '星澜BudsPlus.md']`

**h029**　星澜 Sound 的价格是多少？

- 期望：`['星澜Sound.md']`
- dense 排名：2　top3：`['星澜SoundStandard.md', '星澜Sound.md', '星澜SoundPlus.md']`
- hybrid 排名：1　top3：`['星澜Sound.md', '星澜SoundPro.md', '星澜SoundSE.md']`

**h031**　星环 Watch 的质保年限是多少？

- 期望：`['星环Watch.md']`
- dense 排名：2　top3：`['星环WatchStandard.md', '星环Watch.md', '星环WatchPro.md']`
- hybrid 排名：1　top3：`['星环Watch.md', '星环WatchAir.md', '星环WatchPlus.md']`

**h037**　星河 洗衣机 的价格是多少？

- 期望：`['星河洗衣机.md']`
- dense 排名：2　top3：`['星河洗衣机Standard.md', '星河洗衣机.md', '星河洗衣机Air.md']`
- hybrid 排名：1　top3：`['星河洗衣机.md', '星河洗衣机Pro.md', '星河洗衣机Air.md']`

**h038**　星河 洗衣机 的价格是多少？

- 期望：`['星河洗衣机.md']`
- dense 排名：2　top3：`['星河洗衣机Standard.md', '星河洗衣机.md', '星河洗衣机Air.md']`
- hybrid 排名：1　top3：`['星河洗衣机.md', '星河洗衣机Pro.md', '星河洗衣机Air.md']`

**h039**　星河 洗衣机 的质保年限是多少？

- 期望：`['星河洗衣机.md']`
- dense 排名：2　top3：`['星河洗衣机Standard.md', '星河洗衣机.md', '星河洗衣机Air.md']`
- hybrid 排名：1　top3：`['星河洗衣机.md', '星河洗衣机Air.md', '星河洗衣机Pro.md']`

**h042**　星河 净化器 的噪音是多少？

- 期望：`['星河净化器.md']`
- dense 排名：2　top3：`['星河净化器Standard.md', '星河净化器.md', '星河净化器Plus.md']`
- hybrid 排名：1　top3：`['星河净化器.md', '星河净化器Standard.md', '星河净化器Mini.md']`

**h052**　星域 Air Air 的质保年限是多少？

- 期望：`['星域AirAir.md']`
- dense 排名：2　top3：`['星域Air.md', '星域AirAir.md', '星域AirStandard.md']`
- hybrid 排名：1　top3：`['星域AirAir.md', '星域Air.md', '星域PadAir.md']`

**h100**　星河 空调 和 星河 空调 Pro 的噪音分别是多少？请两款都给出。

- 期望：`['星河空调.md', '星河空调Pro.md']`
- dense 排名：2　top3：`['星河空调ProPlus.md', '星河空调Pro.md', '星河空调ProMaxPlus.md']`
- hybrid 排名：1　top3：`['星河空调Pro.md', '星河空调ProPlus.md', '星河空调ProMax.md']`

**h117**　星辰 X1 Pro+ 和 星辰 X1 Ultra 的质保年限分别是多少？请两款都给出。

- 期望：`['星辰X1ProPlus.md', '星辰X1Ultra.md']`
- dense 排名：2　top3：`['星辰X1ProMaxPlus.md', '星辰X1Ultra.md', '星辰X1ProPlus.md']`
- hybrid 排名：1　top3：`['星辰X1Ultra.md', '星辰X1ProPlus.md', '星辰X1ProMaxPlus.md']`
