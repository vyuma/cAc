# Arena.py
import sys
import coyote
import time
import json
import os
import random
from tqdm import tqdm  
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from client.not_websocket_client import Client as LocalClient

class Arena:
    """
    WebSocketなしで、複数クライアント(=インスタンス)を
    指定した回数だけ対戦させるクラス。
    ebSocket を使わずに、複数の LocalClient インスタンスと対戦させる。
    """

    def __init__(self, total_matches=3, predefined_clients=None):
        """
        total_matches: 対戦回数
        predefined_clients: [[client_instance, "client_name"], [...]] のように
                           あらかじめ渡しておきたいクライアントのリスト
        """
        self.game_num = 0
        self.current_game_index = 0
        self.round_num = 0

        self.players = []        # [LocalClient, LocalClient, ...]
        self.active_players = [] # 生存中プレイヤー(ライフ>0)だけ入れる
        self.deck = coyote.game.Deck()  # coyoteデッキ
        self.win_count_map = {}
        self.logs = {"round_info": []}
        self.death_order = []  # 死亡順
        self.progress_bar = None
        self.use_tqdm = True  # tqdmを使うかどうか
        self.turn_index = 0
        
        self.is_shuffle_card = False
        self.is_double_card = False
        
        # 例: [[LocalClient(...), "AI_1"], [SomeOtherAIClient(...), "MyAI"]]
        self.predefined_clients = predefined_clients if predefined_clients else []

    def setup_arena(self):
        """
        1) もしコード上で渡されているクライアントがあれば追加するか聞く
        2) ゲーム数を聞く
        3) ユーザがプレイするかどうかを聞く
        4) 追加の参加者を募集
        """
        # 1. もし事前に定義されているクライアントがあれば、追加するかを尋ねる
        if self.predefined_clients:
            use_predefined = input(
                f"事前に {len(self.predefined_clients)} 個のクライアントが定義されています。"" これらをゲームに追加しますか？ (y/N):" ).strip().lower() == 'y'

            if use_predefined:
                for client_instance, client_name in self.predefined_clients:
                    client_instance.player_name = client_name

                    self.players.append(client_instance)
                    self.win_count_map[client_instance.player_name] = 0
                self._log(f"事前定義された {len(self.predefined_clients)} 個のクライアントを追加しました。")

        # 2. 対戦回数を入力
        user_input_game_num = input(
            f"対戦するゲーム数を入力してください (Default=3): ").strip() or "3"
        if user_input_game_num.isdigit():
            self.game_num = int(user_input_game_num)

        # 3. 自分がプレイするかどうか
        is_human_play = input("あなたはプレイヤーとして参加しますか？ (y/N): ").strip().lower() == 'y'
        if is_human_play:
            my_name = input("あなたのプレイヤー名を入力してください (例: PlayerMe): ") or "PlayerMe"
            my_client = LocalClient(player_name=my_name, is_ai=False)
            self.players.append(my_client)
            self.win_count_map[my_name] = 0
            self.use_tqdm = False  # tqdmを使わないようにする

        # 4. あと何人追加するか聞く（最低2人必要なので、すでに何人いるか考慮）
        current_player_count = len(self.players)
        if current_player_count < 2:
            self._log(f"現在プレイヤー数: {current_player_count} 人。最低2人必要です。")
            num_needed = 2 - current_player_count
            num_players_to_add = int(input(f"あと何人追加しますか？ (最低 {num_needed} 人): ") or num_needed)
            if num_players_to_add < num_needed:
                num_players_to_add = num_needed
        else:
            # ユーザーに「追加でAI/プレイヤーを入れますか？」を聞いて任意の人数を足してもらう
            num_players_to_add = input("追加のプレイヤー数を入力してください (0=追加なし): ").strip()
            if not num_players_to_add.isdigit():
                num_players_to_add = 0
            else:
                num_players_to_add = int(num_players_to_add)

        # 追加のプレイヤー（AI）
        for i in range(num_players_to_add):
            default_name = f"AI_{i+1 + current_player_count}"
            name = input(f"{i+1} 人目の名前を入力してください (default={default_name}): ") or default_name
            c = LocalClient(player_name=name, is_ai=True)
            self.players.append(c)
            self.win_count_map[name] = 0

        # 結果を表示
        self._log("\n[最終的な参加プレイヤー一覧]")
        for p in self.players:
            self._log(f"  {p.player_name} (is_ai={p.is_ai})")
    
    def _log(self, message: str):
        if self.use_tqdm:
            tqdm.write(message)
            if self.progress_bar:
                self.progress_bar.refresh()
        else:
            print(message)
                
    def run(self):
        """
        Arenaのメインループ:
          1. setup_arena()でプレイヤーやゲーム数を決定
          2. 指定回数だけゲームを進行
          3. 終了したらログや戦績を出力
        """
        self.setup_arena()
        
        if self.use_tqdm:
            self.progress_bar = tqdm(total=self.game_num, desc="進行中のゲーム", unit="game", leave=True, dynamic_ncols=True)
        
        for game_i in range(1, self.game_num + 1):
            self._log(f"\n=== GAME {game_i}/{self.game_num} START ===")
            self.start_game(game_i)
            if self.use_tqdm and self.progress_bar:
                self.progress_bar.update(1) 
            
        if self.use_tqdm:
            self.progress_bar.close()
        # すべて終了したら集計
        self._log("\n=== 全ゲーム終了 ===")
        self.show_final_result()
        self.save_log_json()
        
    def start_game(self, game_index):
        """
        server.py の game_start 相当:
        - プレイヤーライフを初期化
        - active_players リセット
        - デッキをリセット
        - round_num=0
        - roundを繰り返す
        """
        self.current_game_index = game_index
        self.round_num = 0

        # プレイヤーのライフなど初期化
        for p in self.players:
            p.life = 3
            
        self.active_players = self.players[:]
        self.deck.reset()

        # 一度に何度もラウンドを回して、最後の1人になるまで続行
        while len(self.active_players) > 1:
            self.round_num += 1
            self.round_start()

        # 勝者が残っていればwin_count +1
        if len(self.active_players) == 1:
            winner = self.active_players[0]
            self.win_count_map[winner.player_name] += 1
            self._log(f"[GAME {game_index} RESULT] Winner: {winner.player_name}")
        else:
            # 全滅 or 想定外
            self._log(f"[GAME {game_index} RESULT] No winners...")
        
        # winner は最後まで死んでいないので death_ranking には含まれない
        if len(self.death_order) > 0:
            self._log(f"Death Ranking (first -> last): {self.death_order}")
        else:
            self._log("No one died this game.")
        self.death_order = []  # 次のゲームのためにリセット
          

    def round_start(self):
        """
        ラウンド開始 (server.py の round_start 相当):
        - デッキから各activeプレイヤーがカード1枚引く
        - logs["round_info"] にラウンド情報を追加
        - turn_start(順番にhandle_turn)を回す
        - コヨーテが起きるか、全員が回って次ラウンドへ行くか
        """
        # カードを引く
        for p in self.active_players:
            if self.deck.cards == []:
                self._log("Deck is empty. Resetting the deck.")
                self.deck.reset()
            card = self.deck.draw()
            p.hold_card = card 

        # ラウンドログを作る
        self.logs["round_info"].append({
            "round_count": self.round_num,
            "player_info": [
                {
                    "name": p.player_name,
                    "life": p.life,
                    "card": p.hold_card
                } for p in self.players
            ],
            "deck": self.deck.cards.copy(),  # デッキの残り
            "turn_info": []
        })

        # 全員が一巡するまで turn_start() を回す例
        # (コヨーテが発生してround_endになる場合もある)
        last_call = 0
        turn_count = 0
        while (len(self.active_players) > 1):
            current_player = self.active_players[self.turn_index % len(self.active_players)]

            print(f"get_others_info: {self.get_others_info(current_player, self.active_players)}")
            # otherカード合計
            other_cards = [p.hold_card for p in self.active_players if p != current_player]
            
            # sum_of_others = self.sum_of_others_cards(other_cards)
            sum_of_others = self.convert_card(other_cards, True)
            legal_actions = [-1, last_call+1, 120]

            # turn_handling相当
            turn_data = {
                "header": "turn",
                "player_sid": None,  # 実際は使わない
                "others_info": self.get_others_info(current_player, self.active_players),
                "sum": sum_of_others,
                "round_num": self.round_num,
                "log": self.logs["round_info"][-1]["turn_info"],  # これまでのturnログ
                "legal_action": legal_actions
            }
            action = current_player.handle_turn(turn_data)
            if self.use_tqdm:
                self.progress_bar.refresh()

            # ログ追加
            self.logs["round_info"][-1]["turn_info"].append({
                "turn_count": turn_count,
                "turn_player": current_player.player_name,
                "action": action
            })

            # アクションが -1 = コヨーテ なら round_end
            if action == -1:
                self.round_end(current_player, last_call)
                break
            else:
                # last_callを更新し、次のプレイヤーへ
                last_call = action
                self.turn_index = (self.turn_index + 1) % len(self.active_players)
                
            turn_count += 1
            
    def get_others_info(self,current_player, active_players):
        current_index = active_players.index(current_player)
        next_index = (current_index + 1) % len(active_players)
        prev_index = (current_index - 1) % len(active_players)
        next_player = active_players[next_index]
        prev_player = active_players[prev_index]
        
        return [
            {
                "name": p.player_name,
                "card_info": p.hold_card,  # 自分からは見えない
                "life": p.life,
                "is_next": p == next_player,
                "is_prev": p == prev_player,
            } for p in active_players if p != current_player
        ]
    
    def convert_card(self, data, Is_othersum):
        return coyote.game.convert_card(self, data, Is_othersum, self.deck)
    
    # #場のカードの合計値を計算する
    def calc_card_sum(self, true_cards):
        return coyote.game.calc_card_sum(self, true_cards)
    
    def sum_of_others_cards(self,cards):
        true_cards = sorted(cards, reverse=True)
        index = 0

        while index < len(true_cards):
            card = true_cards[index]

            # ？カード（103）は、0として扱う
            if card == 103:
                true_cards[index] = 0

            # MAXカード（102）は最大通常カードを0にし、自分も0
            elif card == 102:
                normal_cards = [c for c in true_cards if c < 100]
                if normal_cards:
                    max_card = max(normal_cards)
                    max_index = true_cards.index(max_card)
                    true_cards[max_index] = 0
                true_cards[index] = 0

            # SHUFFLEカード（101）は0として扱い、フラグを立てる
            elif card == 101:
                true_cards[index] = 0

            index += 1

        return sum(true_cards)
        
    def round_end(self, coyote_player, last_call):
        """
        server.py の round_end 相当:
        - コヨーテ成功or失敗判定
        - 該当プレイヤーのライフを減らし、死んだら activeから除外
        """
        total_sum = self.convert_card((p.hold_card for p in self.active_players),False)
        # コヨーテ成功判定 
        is_coyote_success = (last_call > total_sum)

        # 前のプレイヤーを探す
        c_idx = self.active_players.index(coyote_player)
        prev_idx = (c_idx - 1) % len(self.active_players)
        prev_player = self.active_players[prev_idx]

        if self.is_shuffle_card:
            # SHUFFLEカードを引いた => 山札をリセット
            self.deck.reset()
            self.is_shuffle_card = False
            
        if is_coyote_success:
            # コヨーテ成功 => 前プレイヤーがライフ-1
            self._log(f"{coyote_player.player_name} called COYOTE successfully! total_sum is {total_sum},last_call is {last_call},{prev_player.player_name} loses 1 life.")
            prev_player.life -= 1
            self.turn_index = prev_idx
            if prev_player.life <= 0:
                self._log(f"{prev_player.player_name} is dead!")
                self.active_players.remove(prev_player)
                self.death_order.append(prev_player.player_name)
                # self.turn_index = c_idx

        else:
            # コヨーテ失敗 => コールした本人がライフ-1
            self._log(f"{coyote_player.player_name} called COYOTE but failed! total_sum is {total_sum},last_call is {last_call},They lose 1 life.")
            coyote_player.life -= 1
            self.turn_index = c_idx
            if coyote_player.life <= 0:
                self._log(f"{coyote_player.player_name} is dead!")
                self.active_players.remove(coyote_player)
                self.death_order.append(coyote_player.player_name)
                self.turn_index = prev_idx

    def show_final_result(self):
        """
        全ゲーム終了後の集計
        """
        self._log("\n=== [FINAL RESULT] ===\n")
        for name, wins in self.win_count_map.items():
            self._log(f"{name}: {wins} wins")
    
    def save_log_json(self):
        log_folder = "./log/"
        retry_count = 0

               # ログフォルダが存在しない場合は作成
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)

        while retry_count < 3:
            try:
                filename = str(time.time())
                filefullpath = os.path.join(log_folder, filename + ".json")
                with open(filefullpath, 'x', encoding='UTF-8') as f:
                  json.dump(self.logs, f)
                self._log(f"Successfully saved the log file as {filefullpath}.")
                break
            except FileExistsError:
                retry_count += 1
                time.sleep(0.1)
                self._log("Retry save the log file")
            except Exception as e:
                self._log(f"Failed to write to the log file: {e}")
        self._log("Failed to write to the log file after multiple attempts.")


if __name__ == "__main__":
      # -- Example usage --
    #
    # もし事前に定義したクライアントを渡したい場合はこんな感じ:
    #
    # from client.sample_arena_client import SampleClient as SampleClient
    
    # predefs = [
    #     [SampleClient(player_name="PreAI1", is_ai=True), "PreAI1"],
    #     [SampleClient(player_name="PreAI2", is_ai=True), "CustomAI"]
    # ]
    
    # arena = Arena(total_matches=5, predefined_clients=predefs)
    # arena.run()
    #
    # 今は特に事前クライアントを指定せずに起動
    
    arena = Arena()
    arena.run()
